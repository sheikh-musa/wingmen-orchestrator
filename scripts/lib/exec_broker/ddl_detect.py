#!/usr/bin/env python3
# Vendored verbatim from ihsanos-irsyad scripts/db/lib/ddl_detect.py (sha256
# 02a4c047d7ad196dc408f3cce384dcc8da5388d6fb26bcd14f29a726c78c453e) into
# exec_broker's own package so the broker stays self-contained and deployable
# to the dedicated irsyad-exec unix user without depending on that user having
# read access into the rest of this checkout (see this repo's
# scripts/hooks/ddl_detect.py for the orchestrator's OTHER vendored copy of the
# same upstream file). Re-sync by re-copying verbatim + updating this pin;
# never hand-edit either vendored copy to diverge from the other.
"""ddl_detect.py — the ONE DDL-shape detector shared between exec_prod.py
(this repo) and the orchestrator's Rule-G pre-command hook (console #56685,
coord #56687, q#206 follow-up). Two independently-implemented detectors that
could disagree on the same SQL is exactly the footgun this file exists to
prevent: BOTH sides invoke (or vendor, pinned by sha256) this SAME module —
never re-derive the logic in a second place.

DDL-shaped = a top-level CREATE/ALTER/DROP/TRUNCATE (of any object),
GRANT/REVOKE … ON, or a top-level bare INTO (SELECT … INTO creates a table —
excluded when it's part of INSERT INTO or MERGE INTO, both plain DML) — OR a
CREATE/ALTER/DROP/TRUNCATE/GRANT/REVOKE keyword hidden inside a dollar-quoted
DO/function body — either written literally (CREATE TABLE … AS inside a DO)
or as a dynamic-EXECUTE string argument (EXECUTE format('CREATE TABLE %I
(...)', v)). Conservative by construction: a DDL keyword inside a TOP-LEVEL
comment or string literal is ignored (so DML-only SQL needs no gate), but one
inside a dollar-quoted body is not — that is exactly where dynamic DDL hides.
A bare INTO inside a dollar body means PL/pgSQL variable assignment (SELECT x
INTO v), not table creation, so the body scan does not apply this rule. This
is correct, not merely unapplied: PL/pgSQL itself refuses a dynamic `SELECT
... INTO TABLE` (EXECUTE of SELECT ... INTO is not implemented) and static
`SELECT ... INTO TABLE n` inside a function/DO body is a variable-assignment
INTO that errors on the bare TABLE keyword — PG never lets either form
create a table from inside a body, so no gate is needed there regardless.

LEXER BLIND SPOTS CLOSED HERE (whole-branch review + bus #79239, 2026-10-28):
this is a best-effort single-pass lexer, not a full PG parser, and had three
proven blind spots that could swallow real top-level code:

1. No double-quoted-IDENTIFIER state: a bare ', --, /* or $tag$ that
   textually followed a "-quoted identifier opened a bogus noise region as
   if it were bare code (e.g. SELECT "x--y"; DROP TABLE t hid the DROP inside
   what looked like a line comment). Fixed by giving "..." its own state in
   every lexer below, exactly mirroring '...'  — doubled "" is the escaped-
   quote-inside-identifier rule, same shape as '' inside a string.

2. $ is a legal PG identifier-continuation character, so col$tag$ is ONE
   identifier token to PG, but the dollar-open regex matched $tag$ mid-
   identifier anyway, opening a bogus dollar body. Not a demonstrated miss
   against is_ddl_shaped itself (anything swallowed this way is still a
   dollar body and still gets DDL-keyword-rescanned) — fixed anyway for
   correctness/consistency: $ directly after an identifier character is
   never treated as an opener.

3. standard_conforming_strings=off (a plain session GUC any caller can flip
   mid-statement with SET or set_config(), no privilege needed) makes PG
   treat backslash as an escape character inside ordinary '...' strings too,
   not just E'...' ones. Which of two readings is correct for a given
   backslash-before-quote is GENUINELY AMBIGUOUS without knowing the runtime
   GUC value, and picking either one as a hard default is unsafe in the OTHER
   mode (verified both directions before settling on this fix — assuming
   SCS=off by default would instead let a real top-level '\' content char
   under SCS=on swallow everything after it as "still string"). The only
   safe resolution is to not resolve it: is_ddl_shaped refuses to analyze any
   inner_sql containing a backslash ANYWHERE and treats it as DDL-shaped
   unconditionally, the same posture the orchestrator's exec_broker
   lexical_hazard pre-screen already takes (PR #570) — false refusal, never
   a missed write.

Importable API:
    from ddl_detect import is_ddl_shaped
    is_ddl_shaped(sql: str) -> bool

CLI (for a consumer that cannot import Python across repos, e.g. the
orchestrator hook):
    python3 ddl_detect.py <path-to-sql-file>
      exit 0  = DML-only, no --gate required
      exit 10 = DDL-shaped, a --gate is required
      exit 2  = file unreadable / usage error

Stdlib only. No network, no DB connection, no side effects — a pure text
classifier over the SQL string you give it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_DDL_KEYWORD_RE = re.compile(r"(?i)\b(?:CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b")

# Opening of a dollar-quote tag ($$ or $tag$). Used only from inside the single-pass
# lexers below (NOT as a standalone sub) — the whole point is that a `$function$` that
# appears inside a -- comment or a '...' string is NOT a dollar-quote opener, which a
# regex sub over the raw text cannot know. (A `$function$` in a comment mispairing with
# a real tag is exactly what false-refused mig381 — cp#122, 2026-10-01, in exec_prod.py's
# own history before this module existed.)
_DOLLAR_OPEN_RE = re.compile(r"\$([A-Za-z_]\w*|)\$")

# A top-level bare INTO (SELECT ... INTO creates a table) vs. INSERT INTO or
# MERGE INTO (both plain DML — MERGE is PG15+ upsert syntax, not a table-
# creating statement) — tried in this order so INSERT/MERGE INTO is consumed
# whole and never separately re-matched as a bare INTO.
_INTO_RE = re.compile(r"(?i)\b(?:insert|merge)\s+into\b|\binto\b")


def _is_identifier_char(ch: str) -> bool:
    """ASCII alnum, underscore, dollar, or any Unicode letter/digit (PG
    identifiers may contain non-ASCII letters) — broad on purpose: erring
    broad only means treating MORE $ signs as identifier-internal, never
    missing a real dollar-quote opener that follows actual non-identifier
    code."""
    return ch.isalnum() or ch in ("_", "$")


def _has_bare_into(stripped_sql: str) -> bool:
    for m in _INTO_RE.finditer(stripped_sql):
        word = m.group(0).lower()
        if not (word.startswith("insert") or word.startswith("merge")):
            return True
    return False


def strip_sql_noise(sql: str) -> str:
    """Blank out dollar-quoted bodies, line/block comments and single-quoted strings
    so a statement-level keyword scan sees only real top-level SQL tokens.

    SINGLE PASS, context-aware: at each position we are in exactly one of {code,
    line-comment, block-comment, string, dollar-body}, so a `$tag$` / `--` / `/*` / `'`
    is only significant when we are in `code`. This is what makes a `$function$` inside
    a comment (or a `--` inside a dollar body) harmless. Best-effort (not a full
    parser): PG block-comment NESTING is handled; odd edge cases degrade to leaving
    text in place, which can only cause a FALSE REFUSAL (fail-safe for a screen), never
    a missed real top-level keyword that then runs unguarded."""
    out = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i:i + 2]
        if two == "--":                                   # line comment → to EOL
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
            continue
        if two == "/*":                                   # block comment (PG: nestable)
            depth, i = 1, i + 2
            while i < n and depth:
                if sql[i:i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif sql[i:i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
            continue
        if ch == "'":                                     # single-quoted string ('' escapes)
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append("''")
            continue
        if ch == '"':                                     # quoted identifier ("" escapes)
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append('""')
            continue
        if ch == "$" and i > 0 and _is_identifier_char(sql[i - 1]):
            # $ immediately after an identifier char is part of THAT
            # identifier to PG, not a dollar-quote opener — pass through as
            # ordinary code rather than risk opening a bogus body.
            out.append(ch)
            i += 1
            continue
        m = _DOLLAR_OPEN_RE.match(sql, i)                 # dollar-quoted body
        if m:
            tag = m.group(0)
            close = sql.find(tag, m.end())
            if close == -1:                               # unterminated — leave as-is
                out.append(sql[i:])
                break
            i = close + len(tag)
            out.append(" $BODY$ ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_comments_only(sql: str) -> str:
    """Blank `--` line comments and `/* */` block comments, leaving every string
    and dollar-quoted body BYTE-FOR-BYTE intact. Used on the content of a
    dollar-quoted body (DO/function) for the DDL-shape scan below — unlike
    `strip_sql_noise`, this deliberately does NOT blank single-quoted strings,
    because a dynamic-DDL string argument (`EXECUTE format('CREATE TABLE %I
    (...)', v_name)`) must still be visible to the scan (see `is_ddl_shaped`)."""
    out = []
    i, n = 0, len(sql)
    while i < n:
        two = sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
            continue
        if two == "/*":
            depth, i = 1, i + 2
            while i < n and depth:
                if sql[i:i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif sql[i:i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
            continue
        if sql[i] == '"':                                 # quoted identifier, preserved verbatim
            out.append(sql[i])
            i += 1
            while i < n:
                out.append(sql[i])
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 1
                        out.append(sql[i])
                        i += 1
                        continue
                    i += 1
                    break
                i += 1
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


def extract_dollar_bodies(sql: str) -> list[str]:
    """Return the RAW text of every dollar-quoted body in sql (DO $$…$$ blocks,
    function bodies), in the same single-pass, context-aware lexer as
    `strip_sql_noise` — but COLLECTING each body's content instead of blanking
    it, so a DDL keyword written literally inside a DO/function body (or inside
    a string argument to EXECUTE/format within one) is still visible to the
    DDL-shape scan. Comments and single-quoted strings OUTSIDE a dollar body are
    skipped over (not collected) exactly as `strip_sql_noise` does, so a `$tag$`
    inside a `--` comment or a `'...'` string is correctly not treated as an
    opener."""
    bodies: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "/*":
            depth, i = 1, i + 2
            while i < n and depth:
                if sql[i:i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif sql[i:i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            continue
        if ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == '"':
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "$" and i > 0 and _is_identifier_char(sql[i - 1]):
            i += 1
            continue
        m = _DOLLAR_OPEN_RE.match(sql, i)
        if m:
            tag = m.group(0)
            close = sql.find(tag, m.end())
            if close == -1:
                break
            bodies.append(sql[m.end():close])
            i = close + len(tag)
            continue
        i += 1
    return bodies


def is_ddl_shaped(inner_sql: str) -> bool:
    """True if inner_sql is DDL-shaped: a top-level CREATE/ALTER/DROP/TRUNCATE
    of an object, or a GRANT/REVOKE … ON — OR a dollar-quoted DO/function body
    that contains one of those keywords literally (CREATE TABLE … AS inside a
    DO) or as a dynamic-EXECUTE string argument (EXECUTE format('CREATE
    TABLE…', …)). Conservative by construction: the top-level scan reuses
    `strip_sql_noise` (so a DDL keyword inside a top-level comment or an
    unrelated string literal is correctly ignored), but the dollar-body scan
    deliberately does NOT strip strings (only comments), because that is
    exactly where dynamic DDL hides. DML-only SQL (SELECT/INSERT/UPDATE/DELETE,
    a DO block with only DML inside, set_config) has no DDL keyword anywhere
    in either scan and needs no gate.

    Any backslash ANYWHERE in inner_sql short-circuits to True before any
    lexing runs at all (see module docstring, blind spot 3) — whether a
    backslash before a quote closes a string depends on the session's
    standard_conforming_strings GUC, which this static module cannot know,
    and guessing either way is unsafe in the other mode."""
    if "\\" in inner_sql:
        return True
    stripped = strip_sql_noise(inner_sql)
    if _DDL_KEYWORD_RE.search(stripped) or _has_bare_into(stripped):
        return True
    for body in extract_dollar_bodies(inner_sql):
        if _DDL_KEYWORD_RE.search(strip_comments_only(body)):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: ddl_detect.py <path-to-sql-file>", file=sys.stderr)
        return 2
    try:
        text = Path(args[0]).read_text(encoding="utf-8")
    except OSError as e:
        print(f"ddl_detect.py: cannot read {args[0]}: {e}", file=sys.stderr)
        return 2
    if is_ddl_shaped(text):
        print("DDL-SHAPED")
        return 10
    print("DML-ONLY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
