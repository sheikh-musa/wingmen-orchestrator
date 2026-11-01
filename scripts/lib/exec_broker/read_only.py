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
ddl_detect (strip_sql_noise / extract_dollar_bodies), and DDL detection is
is_ddl_shaped itself. Never hand-edit the vendored file and never re-derive
its context-aware lexing here — a second, divergent lexer is exactly the
footgun the vendored copy exists to prevent.

LEXICAL HAZARD PRE-SCREEN: this module imports is_lexically_hazardous from
the shared lexical_hazard module (see that module's docstring for the full
two-class rationale — double-quoted-identifier noise-opener confusion, plus
the identifier-$ dollar-open bypass) rather than defining its own copy.
gate_validation.require_gate reuses the SAME function on the base DDL-gate
path (rollout step (c), bus #79161) — one shared definition so the two
pre-screens cannot drift, the same reasoning as ref_override_is_live being
the one liveness definition shared across the override checks.

KNOWN LIMITATION (same doctrine as ddl_detect): this is a best-effort TEXT
screen, not a semantic analyzer. A read-shaped statement that calls an
existing user-defined write-capable FUNCTION by name is indistinguishable
from a read by text alone. Compensating controls: the override window is
bounded (<=24h, auto-expiring), every call's exact SQL is audited to
exec_broker_access_log before validation, and arming requires a
console-gated root action.

FAIL-CLOSED DOCTRINE: false refusals are acceptable; a missed write is not.
Concretely, a quoted column named "update"/"into"/"for", a dollar-quoted
string constant containing write words (incl. into/for), and anything
is_lexically_hazardous flags are all REFUSED — the same deliberate
over-refusal stance ddl_detect takes ("a false refusal is fail-safe for a
screen"). A caller hitting one renames or re-quotes; nothing legitimate is
silently let through the other way.
"""
from __future__ import annotations

import re
from datetime import datetime

from .ddl_detect import extract_dollar_bodies, is_ddl_shaped, strip_sql_noise
from .gate_validation import BrokerValidationError, ref_override_is_live
from .lexical_hazard import is_lexically_hazardous
from .protocol import ErrorCode


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
# lo_open/lo_write/lo_close included: lo_write(lo_open(...)) mutates an
# existing large object from a single read-shaped statement. The full
# advisory-lock family and pg_stat_reset_slru complete their groups
# (whole-branch review Important #2 / Minor #6).
_FORBIDDEN_FUNCTION_TOKENS = frozenset({
    "set_config", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_notify", "dblink", "dblink_exec", "dblink_connect", "dblink_disconnect",
    "lo_open", "lo_write", "lo_close", "lo_import", "lo_export", "lo_create",
    "lo_put", "lo_truncate", "lo_unlink",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_file_write", "pg_file_rename", "pg_file_unlink", "pg_file_read",
    "pg_advisory_lock", "pg_advisory_lock_shared", "pg_advisory_xact_lock",
    "pg_advisory_xact_lock_shared", "pg_try_advisory_lock",
    "pg_try_advisory_lock_shared", "pg_advisory_unlock",
    "pg_advisory_unlock_shared", "pg_advisory_unlock_all",
    "pg_stat_reset", "pg_stat_reset_shared", "pg_stat_reset_slru",
    "nextval", "setval",
})

# Inside a dollar-quoted body (reachable today only as a dollar-quoted string
# constant in a read-shaped statement, since DO/CREATE heads are already
# refused by the head whitelist and is_ddl_shaped — kept as belt-and-braces
# against exactly where dynamic SQL hides): dynamic EXECUTE + DML writes.
# into/for included (PR #569 review, belt-and-braces half): a body that gets
# mis-located by the identifier-$ hazard below would otherwise hide a
# SELECT...INTO or a locking FOR clause from every other scan. DDL keywords
# inside bodies are already is_ddl_shaped's job.
_FORBIDDEN_BODY_TOKENS = frozenset({"insert", "update", "delete", "merge", "execute", "into", "for"})


def _word_regex(tokens: frozenset) -> "re.Pattern[str]":
    return re.compile(r"(?i)\b(?:" + "|".join(sorted(tokens)) + r")\b")


_CODE_TOKEN_RE = _word_regex(_FORBIDDEN_CODE_TOKENS)
_FUNCTION_TOKEN_RE = _word_regex(_FORBIDDEN_FUNCTION_TOKENS)
_BODY_TOKEN_RE = _word_regex(_FORBIDDEN_BODY_TOKENS)

_HEAD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

# Console amended ruling C + addendum (bus #89846/#89875): every gate-free
# plain read is executed as READ_ONLY_PROLOGUE + sql in ONE mgmt-API query
# string (one implicit transaction). These transaction-control / session-
# setting words must therefore never appear in CODE position (outside string
# literals, dollar bodies, comments and quoted identifiers -- all blanked by
# the vendored strip_sql_noise first). Deliberately over-refuses: a bare,
# unquoted column named e.g. `release` or `set` is refused too.
READ_ONLY_PROLOGUE = "SET TRANSACTION READ ONLY; "
_TXN_CONTROL_WORD_RE = re.compile(
    r"(?i)\b(?:commit|rollback|begin|abort|savepoint|release|set|reset|discard)\b"
)
_TXN_CONTROL_PHRASE_RE = re.compile(r"(?i)\b(?:start|prepare)\s+transaction\b")
_END_RE = re.compile(r"(?i)\bend\b")
_CASE_RE = re.compile(r"(?i)\bcase\b")
# What strip_sql_noise leaves behind for a CLOSED dollar body.
_CLOSED_BODY_MARK = " $BODY$ "
_RAW_DOLLAR_OPEN_RE = re.compile(r"\$(?:[A-Za-z_]\w*)?\$")
_UNCLOSED_SENTINEL = "\n;select zz_unclosed_sentinel_zz"


def _has_unclosed_region(sql: str) -> bool:
    """True if a string literal, quoted identifier, block comment or dollar
    body is left open (fail closed: the prologue's protection must never sit
    in front of text the lexer could not delimit). An open '...', "..." or
    /* swallows an appended sentinel; an open $tag$ is left raw by
    strip_sql_noise."""
    stripped = strip_sql_noise(sql + _UNCLOSED_SENTINEL)
    if not stripped.endswith("select zz_unclosed_sentinel_zz"):
        return True
    return bool(_RAW_DOLLAR_OPEN_RE.search(stripped.replace(_CLOSED_BODY_MARK, " ")))


def _has_txn_control(stripped: str) -> bool:
    """Transaction-control / session-setting keyword in code position. END is
    allowed only as a CASE terminator: more END tokens than CASE tokens means
    a bare END (a COMMIT synonym) -- refused."""
    if _TXN_CONTROL_WORD_RE.search(stripped) or _TXN_CONTROL_PHRASE_RE.search(stripped):
        return True
    return len(_END_RE.findall(stripped)) > len(_CASE_RE.findall(stripped))

# Raw "..."-quoted identifier content (doubled "" is the escaped-quote rule),
# scanned against the ORIGINAL sql -- not the strip_sql_noise output. The
# vendored lexer's own dquote state (ihsanos PR #1168 re-vendor, bus #79427)
# now correctly blanks identifier content for DDL-shape purposes, which would
# otherwise silently undo this module's documented false-refusal on a quoted
# column named update/into/for/etc (module docstring, FAIL-CLOSED DOCTRINE):
# the two modules want different things from the SAME quoted text, so this
# module keeps its own narrower re-check rather than depending on the
# upstream lexer's (correct, for its own purpose) blanking.
_DQUOTE_IDENTIFIER_RE = re.compile(r'"((?:[^"]|"")*)"')


def _quoted_identifier_contents(sql: str) -> list:
    return [m.group(1).replace('""', '"') for m in _DQUOTE_IDENTIFIER_RE.finditer(sql)]


def is_plain_read(sql: str) -> bool:
    """True iff sql is a plain read: not lexically hazardous, not DDL-shaped
    (is_ddl_shaped), no forbidden write/function token in top-level code
    position, no forbidden function/body token inside any dollar-quoted body,
    and EXACTLY ONE top-level statement, starting with a read keyword
    (SELECT/WITH/TABLE/VALUES). Empty or whitespace-only sql is NOT a plain
    read."""
    # (0) lexical hazard pre-screen (Critical #1): refuse the input class
    #     that can blind the vendored lexer BEFORE any vendored call — the
    #     class defeats is_ddl_shaped below in the same stroke.
    if is_lexically_hazardous(sql):
        return False

    # (a) DDL of any kind — top-level or inside a dollar body — via the
    #     vendored classifier itself.
    if is_ddl_shaped(sql):
        return False

    # (a2) anything the lexer cannot delimit (open string / comment / dollar
    #      body / quoted identifier) is refused outright (bus #89875).
    if _has_unclosed_region(sql):
        return False

    # strip_sql_noise blanks comments, single-quoted strings and dollar
    # bodies, so the scans below see only real top-level code tokens.
    stripped = strip_sql_noise(sql)

    # (a3) transaction-control / session-setting keywords in code position
    #      (bus #89875): COMMIT ROLLBACK BEGIN START TRANSACTION END ABORT
    #      SAVEPOINT RELEASE PREPARE TRANSACTION SET (any) RESET DISCARD.
    if _has_txn_control(stripped):
        return False

    # (b)+(c) writes smuggled into a read-shaped statement in code position:
    # data-modifying CTEs, SELECT ... INTO, FOR UPDATE, set_config, nextval...
    if _CODE_TOKEN_RE.search(stripped) or _FUNCTION_TOKEN_RE.search(stripped):
        return False

    # (b2) a quoted identifier whose content is itself a forbidden word (e.g.
    # a column named "update") -- the vendored lexer's dquote state blanks
    # this from `stripped` above (correct for DDL purposes), so this module's
    # own documented false-refusal needs its own, separate re-check.
    for ident in _quoted_identifier_contents(sql):
        if _CODE_TOKEN_RE.search(ident) or _FUNCTION_TOKEN_RE.search(ident):
            return False

    # (d) dollar bodies get their own scan (comments stripped, strings kept —
    #     where dynamic SQL hides). Today these only occur in a read-shaped
    #     statement as dollar-quoted string constants; refusing on their
    #     content is a deliberate fail-closed false-refusal.
    for body in extract_dollar_bodies(sql):
        if _BODY_TOKEN_RE.search(body) or _FUNCTION_TOKEN_RE.search(body):
            return False

    # (e) EXACTLY ONE top-level statement (trailing ';' allowed), and it must
    #     start with a read keyword. Single-statement is load-bearing (console
    #     amended ruling C, bus #89846): the broker prepends
    #     `SET TRANSACTION READ ONLY;` to every gate-free plain read, and a
    #     second statement is exactly how that would be escaped
    #     (`SELECT 1; COMMIT; DELETE ...`, `...; SET TRANSACTION READ WRITE`,
    #     `...; BEGIN`, `...; RESET ALL`, `...; SET SESSION CHARACTERISTICS ...`).
    #     Fail closed: any multi-statement input, even all-reads, needs a gate.
    statements = [f.strip().lstrip("(").strip() for f in stripped.split(";")]
    statements = [st for st in statements if st]
    if len(statements) != 1:
        return False
    m = _HEAD_RE.match(statements[0])
    return bool(m) and m.group(0).lower() in _READ_FIRST_KEYWORDS


def check_read_only_override(
    guarded_sql: str,
    *,
    project_ref: str,
    ref_override: "str | None",
    ref_override_expires_at: "datetime | None",
    now: "datetime | None" = None,
) -> None:
    """No-op unless the bounded ref override is live AND this request targets
    the override's ref. When both hold, refuse (READ_ONLY_OVERRIDE) any
    guarded_sql that is not a plain read.

    DESIGN RULINGS:
    - SCOPE (cc-fleet-health bus #79039, 2026-10-28 — supersedes the
      broker-wide reading of GO #78974): RO binds only requests whose
      project_ref equals the live override ref. Baseline (always-allowed)
      refs behave exactly as today even while the override is armed. A ref
      that cannot be determined never reaches this check — parse_request
      requires project_ref, and the server runs the ref allowlist check
      BEFORE this one, so an unknown ref is refused as BAD_PROJECT_REF
      before any class check (pinned end-to-end in test_server.py, bus
      #79103).
    - The uid override alone does NOT trigger RO (assignment bus #79024
      names EXEC_BROKER_REF_OVERRIDE_ALIAS only).
    - Liveness comes from gate_validation.ref_override_is_live, the same
      definition check_project_ref uses, so the ref widening and the RO
      mode arm, expire and revert together, by construction — no second
      env var, no second root action to disarm."""
    if not ref_override_is_live(ref_override, ref_override_expires_at, now=now):
        return
    if project_ref != ref_override:
        return
    if is_plain_read(guarded_sql):
        return
    raise BrokerValidationError(
        ErrorCode.READ_ONLY_OVERRIDE,
        "ref override is active for this project_ref -- broker is read-only "
        "for this window; statement is not a plain read "
        "(SELECT/WITH/TABLE/VALUES)",
    )
