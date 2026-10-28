#!/usr/bin/env python3
"""read_only.py — hc#95 exec-broker plain-read classifier + read-only-override
check (orch-console GO #78974, assignment bus #79024).

WHY THIS EXISTS: the bounded ref override (op#34322/#76985/#76989, PR#513)
widens which project_ref the broker will call for a wall-clock-bounded window,
and every arm authorization so far has been scoped READ-ONLY by request (e.g.
the goumlyne deep-dive arm, bus #78968/#78974) — but ddl_detect classifies
SELECT/INSERT/UPDATE/DELETE alike as gate-free "DML-only", so that promise was
enforceable only by the caller's discipline. This module turns it into code:
while the override is live, any guarded_sql that is not a plain read is
refused, fail-closed, with ErrorCode.READ_ONLY_OVERRIDE.

REUSE, NEVER REIMPLEMENT: the lexing comes from the vendored, sha256-pinned
ddl_detect (strip_sql_noise / extract_dollar_bodies / strip_comments_only),
and DDL detection is is_ddl_shaped itself. Never hand-edit the vendored file
and never re-derive its context-aware lexing here — a second, divergent lexer
is exactly the footgun the vendored copy exists to prevent.

KNOWN LIMITATION (same doctrine as ddl_detect): this is a best-effort TEXT
screen, not a semantic analyzer. A read-shaped statement that calls an
existing user-defined write-capable FUNCTION by name is indistinguishable
from a read by text alone. Compensating controls: the override window is
bounded (<=24h, auto-expiring), every call's exact SQL is audited to
exec_broker_access_log before validation, and arming requires a
console-gated root action.

FAIL-CLOSED DOCTRINE: false refusals are acceptable; a missed write is not.
Concretely, a quoted column named "update"/"into"/"for" and a dollar-quoted
string constant containing write words are REFUSED — the same deliberate
over-refusal stance ddl_detect takes ("a false refusal is fail-safe for a
screen"). A caller hitting one renames or re-quotes; nothing legitimate is
silently let through the other way.
"""
from __future__ import annotations

import re
from datetime import datetime

from .ddl_detect import extract_dollar_bodies, is_ddl_shaped, strip_sql_noise


# Every top-level statement must START with one of these (after strip_sql_noise
# has blanked comments/strings/dollar-bodies, and after leading parens). This
# head whitelist is the PRIMARY control; the token scans below are
# defense-in-depth for writes that can hide inside a read-shaped statement.
_READ_FIRST_KEYWORDS = frozenset({"select", "with", "table", "values"})

# Mid-statement tokens that can turn a read-shaped statement into a write:
# data-modifying CTE bodies (WITH d AS (DELETE ...) SELECT), SELECT ... INTO,
# locking reads (FOR UPDATE/FOR SHARE).
#
# Head-only statement keywords (SET, LOCK, COPY, CALL, DO, EXPLAIN, BEGIN,
# COMMENT, VACUUM, ...) are deliberately NOT here: they cannot appear
# mid-statement legally, the head whitelist above already refuses them, and
# many are unreserved words that would false-refuse plain reads of columns
# named e.g. "set"/"lock"/"copy"/"do". Quoted identifiers like "update" in a
# SELECT ARE false-refused by this scan — fail-closed by design (see module
# docstring).
_FORBIDDEN_CODE_TOKENS = frozenset({"insert", "update", "delete", "merge", "into", "for"})

# Write-capable or state-changing functions callable from inside a SELECT.
# Conservative, not exhaustive — the head whitelist is the primary control.
# nextval/setval included: they mutate persistent sequence state.
_FORBIDDEN_FUNCTION_TOKENS = frozenset({
    "set_config", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_notify", "dblink", "dblink_exec", "dblink_connect", "dblink_disconnect",
    "lo_import", "lo_export", "lo_create", "lo_put", "lo_truncate", "lo_unlink",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_file_write", "pg_file_rename", "pg_file_unlink", "pg_file_read",
    "pg_advisory_lock", "pg_advisory_unlock", "pg_advisory_unlock_all",
    "pg_stat_reset", "pg_stat_reset_shared", "nextval", "setval",
})

# Inside a dollar-quoted body (reachable today only as a dollar-quoted string
# constant in a read-shaped statement, since DO/CREATE heads are already
# refused by the head whitelist and is_ddl_shaped — kept as belt-and-braces
# against exactly where dynamic SQL hides): dynamic EXECUTE + DML writes.
# DDL keywords inside bodies are already is_ddl_shaped's job.
_FORBIDDEN_BODY_TOKENS = frozenset({"insert", "update", "delete", "merge", "execute"})


def _word_regex(tokens: frozenset) -> "re.Pattern[str]":
    return re.compile(r"(?i)\b(?:" + "|".join(sorted(tokens)) + r")\b")


_CODE_TOKEN_RE = _word_regex(_FORBIDDEN_CODE_TOKENS)
_FUNCTION_TOKEN_RE = _word_regex(_FORBIDDEN_FUNCTION_TOKENS)
_BODY_TOKEN_RE = _word_regex(_FORBIDDEN_BODY_TOKENS)

_HEAD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def is_plain_read(sql: str) -> bool:
    """True iff sql is a plain read: not DDL-shaped (is_ddl_shaped), no
    forbidden write/function token in top-level code position, no forbidden
    function/body token inside any dollar-quoted body, and every top-level
    statement starts with a read keyword (SELECT/WITH/TABLE/VALUES). Empty or
    whitespace-only sql is NOT a plain read."""
    # (a) DDL of any kind — top-level or inside a dollar body — via the
    #     vendored classifier itself.
    if is_ddl_shaped(sql):
        return False

    # strip_sql_noise blanks comments, single-quoted strings and dollar
    # bodies, so the scans below see only real top-level code tokens.
    stripped = strip_sql_noise(sql)

    # (b)+(c) writes smuggled into a read-shaped statement in code position:
    # data-modifying CTEs, SELECT ... INTO, FOR UPDATE, set_config, nextval...
    if _CODE_TOKEN_RE.search(stripped) or _FUNCTION_TOKEN_RE.search(stripped):
        return False

    # (d) dollar bodies get their own scan (comments stripped, strings kept —
    #     where dynamic SQL hides). Today these only occur in a read-shaped
    #     statement as dollar-quoted string constants; refusing on their
    #     content is a deliberate fail-closed false-refusal.
    for body in extract_dollar_bodies(sql):
        if _BODY_TOKEN_RE.search(body) or _FUNCTION_TOKEN_RE.search(body):
            return False

    # (e) every top-level statement must start with a read keyword.
    saw_statement = False
    for fragment in stripped.split(";"):
        s = fragment.strip().lstrip("(").strip()
        if not s:
            continue
        saw_statement = True
        m = _HEAD_RE.match(s)
        if not m or m.group(0).lower() not in _READ_FIRST_KEYWORDS:
            return False
    return saw_statement
