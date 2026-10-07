#!/usr/bin/env python3
"""ddl_detect.py — the ONE DDL-shape detector shared between exec_prod.py
(this repo) and the orchestrator's Rule-G pre-command hook (console #56685,
coord #56687, q#206 follow-up). Two independently-implemented detectors that
could disagree on the same SQL is exactly the footgun this file exists to
prevent: BOTH sides invoke (or vendor, pinned by sha256) this SAME module —
never re-derive the logic in a second place.

DDL-shaped = a top-level CREATE/ALTER/DROP/TRUNCATE (of any object) or
GRANT/REVOKE … ON, OR one of those keywords hidden inside a dollar-quoted
DO/function body — either written literally (CREATE TABLE … AS inside a DO)
or as a dynamic-EXECUTE string argument (EXECUTE format('CREATE TABLE %I
(...)', v)). Conservative by construction: a DDL keyword inside a TOP-LEVEL
comment or string literal is ignored (so DML-only SQL needs no gate), but one
inside a dollar-quoted body is not — that is exactly where dynamic DDL hides.

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
    in either scan and needs no gate."""
    if _DDL_KEYWORD_RE.search(strip_sql_noise(inner_sql)):
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
