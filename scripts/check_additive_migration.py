#!/usr/bin/env python3
"""check_additive_migration — refuse pre-apply for destructive Supabase migrations.

Per CAI-RESP-102, cc-orchestrator can pre-apply ADDITIVE migrations without
cai review. This linter is the missing safety net: if a migration contains
DROP/TRUNCATE/blanket-UPDATE/blanket-DELETE, exit 1 and require explicit
cai approval first.

Usage:
  scripts/check_additive_migration.py path/to/migration.sql
  scripts/check_additive_migration.py supabase/migrations/*.sql

Exit codes:
  0 — additive only, safe to pre-apply
  1 — destructive operations found (block pre-apply)
  2 — usage error (no file given, file not found)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Comment stripping and allowlist collection
# ---------------------------------------------------------------------------

def strip_sql_comments(sql: str) -> str:
    """Remove -- line comments and /* block comments */ from SQL.

    This prevents false positives from commentary like:
      -- DROP TABLE would be a bad idea
    """
    # Remove block comments first (non-greedy, DOTALL)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Remove line comments
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _collect_allowlisted_lines(text: str) -> set[int]:
    """Lines whose preceding line is `-- linter-allow: <reason>`.

    Returns a set of 1-indexed line numbers that should suppress findings.
    The allowlist only applies to the line immediately after the comment.
    """
    allowed = set()
    lines = text.splitlines()
    # Match `-- linter-allow: <reason>` where reason is non-empty (at least one non-whitespace char)
    pattern = re.compile(r"^\s*--\s*linter-allow:\s*\S", re.IGNORECASE)
    for i, line in enumerate(lines):
        if pattern.match(line):
            # i is 0-indexed; the line immediately after is at index i+1,
            # which is 1-indexed line number i+2
            allowed.add(i + 2)
    return allowed


# ---------------------------------------------------------------------------
# Dollar-quote stripping (plpgsql DO $$ ... $$ blocks)
# ---------------------------------------------------------------------------

_DOLLAR_QUOTE_RE = re.compile(r"\$(\w*)\$.*?\$\1\$", re.DOTALL)


def strip_dollar_quotes(sql: str) -> str:
    """Replace the *body* of dollar-quoted strings with whitespace.

    This prevents the linter from scanning plpgsql procedural code that
    may contain SQL keywords as identifiers or strings.
    The opening/closing $$ tags themselves are left in place so surrounding
    DDL is still parsed correctly.
    """
    return _DOLLAR_QUOTE_RE.sub(lambda m: f"${m.group(1)}$ $${m.group(1)}$", sql)


# ---------------------------------------------------------------------------
# Allow-list: patterns that look destructive but are safe recreate patterns
# ---------------------------------------------------------------------------

# DROP TRIGGER IF EXISTS <name>; ... CREATE TRIGGER <name>
# We'll handle this by checking for the DROP TRIGGER pattern, then looking
# ahead to see if a CREATE TRIGGER with the same name follows.
_DROP_TRIGGER_RECREATE = re.compile(
    r"DROP\s+TRIGGER\s+IF\s+EXISTS\s+(\w+).*?CREATE\s+TRIGGER\s+\1\b",
    re.IGNORECASE | re.DOTALL,
)

# DROP VIEW IF EXISTS <name>; ... CREATE VIEW <name> (or CREATE OR REPLACE VIEW)
_DROP_VIEW_RECREATE = re.compile(
    r"DROP\s+VIEW\s+IF\s+EXISTS\s+(\w+).*?CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+\1\b",
    re.IGNORECASE | re.DOTALL,
)


def build_safe_zones(sql: str) -> set[tuple[int, int]]:
    """Return a set of (start, end) character spans that are safe allowlisted."""
    safe: set[tuple[int, int]] = set()
    for pattern in (
        _DROP_TRIGGER_RECREATE,
        _DROP_VIEW_RECREATE,
        _DROP_CONSTRAINT_RECREATE,
        _DROP_FUNCTION_RECREATE,
    ):
        for m in pattern.finditer(sql):
            safe.add((m.start(), m.end()))
    return safe


def in_safe_zone(pos: int, safe_zones: set[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in safe_zones)


# ---------------------------------------------------------------------------
# Destructive pattern checks
# ---------------------------------------------------------------------------

# Each entry: (label, compiled_regex)
# These are evaluated against stripped SQL (no comments, no dollar-quote bodies).

# DROP TABLE — any qualifier
_DROP_TABLE = re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE)

# DROP COLUMN — standalone or inside ALTER TABLE
_DROP_COLUMN = re.compile(r"\bDROP\s+COLUMN\b", re.IGNORECASE)

# DROP CONSTRAINT
_DROP_CONSTRAINT = re.compile(r"\bDROP\s+CONSTRAINT\b", re.IGNORECASE)

# DROP INDEX
_DROP_INDEX = re.compile(r"\bDROP\s+INDEX\b", re.IGNORECASE)

# DROP TRIGGER — standalone (not immediately followed by CREATE TRIGGER same name)
# We detect the raw pattern; safe recreation zones are excluded afterwards.
_DROP_TRIGGER = re.compile(r"\bDROP\s+TRIGGER\b", re.IGNORECASE)

# DROP FUNCTION — not CREATE OR REPLACE FUNCTION (which is safe)
# We detect DROP FUNCTION and exclude "CREATE OR REPLACE" prefixed patterns.
_DROP_FUNCTION = re.compile(r"\bDROP\s+FUNCTION\b", re.IGNORECASE)

# TRUNCATE
_TRUNCATE = re.compile(r"\bTRUNCATE\b", re.IGNORECASE)

# ALTER TYPE ... DROP VALUE (no cross-statement matching)
_ALTER_TYPE_DROP_VALUE = re.compile(
    r"\bALTER\s+TYPE\b[^;]*?\bDROP\s+VALUE\b", re.IGNORECASE
)

# ALTER COLUMN ... DROP (DROP DEFAULT, DROP NOT NULL — conservative block per CAI-RESP-102)
# Use [^;] to prevent crossing statement boundaries (avoid false positives from
# DOTALL matching across semicolons into unrelated DROP keywords).
_ALTER_COLUMN_DROP = re.compile(
    r"\bALTER\s+COLUMN\b[^;]*?\bDROP\b", re.IGNORECASE
)

# DELETE FROM — we'll post-process to check WHERE clause
_DELETE_FROM = re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE)

# UPDATE — we'll post-process to check WHERE clause
_UPDATE = re.compile(r"\bUPDATE\b", re.IGNORECASE)

# DROP VIEW — standalone (not in a recreate pair)
_DROP_VIEW = re.compile(r"\bDROP\s+VIEW\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Allow-list additions for safe recreate patterns detected in build_safe_zones
# ---------------------------------------------------------------------------

# DROP CONSTRAINT IF EXISTS <name>; ... ADD CONSTRAINT <name>
_DROP_CONSTRAINT_RECREATE = re.compile(
    r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?(\w+).*?ADD\s+CONSTRAINT\s+\1\b",
    re.IGNORECASE | re.DOTALL,
)

# DROP FUNCTION IF EXISTS <name>(...); ... CREATE OR REPLACE FUNCTION <name>
# Match DROP FUNCTION IF EXISTS name followed by CREATE OR REPLACE FUNCTION name
_DROP_FUNCTION_RECREATE = re.compile(
    r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+(\w+)\s*\([^)]*\)\s*;.*?CREATE\s+OR\s+REPLACE\s+FUNCTION\s+\1\b",
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Statement-level helpers for DELETE/UPDATE WHERE analysis
# ---------------------------------------------------------------------------

def _split_statements(sql: str) -> list[tuple[int, str]]:
    """Split SQL into (start_offset, statement_text) pairs by semicolons.

    Skips semicolons inside single-quoted string literals so that a value like
    'pending_review' does not terminate the statement early.

    Dollar-quoted blocks are already stripped before this is called.
    """
    statements: list[tuple[int, str]] = []
    stmt_start = 0
    in_string = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'" and not in_string:
            in_string = True
            i += 1
        elif ch == "'" and in_string:
            # Handle escaped single quote: '' inside a string
            if i + 1 < n and sql[i + 1] == "'":
                i += 2  # skip both quotes — still inside string
            else:
                in_string = False
                i += 1
        elif ch == ";" and not in_string:
            statements.append((stmt_start, sql[stmt_start : i + 1]))
            stmt_start = i + 1
            i += 1
        else:
            i += 1
    # trailing text after last semicolon
    if stmt_start < n:
        tail = sql[stmt_start:]
        if tail.strip():
            statements.append((stmt_start, tail))
    return statements


def _check_delete(stmt: str) -> bool:
    """Return True (BLOCK) if DELETE has no WHERE or WHERE without id restriction."""
    if not _DELETE_FROM.search(stmt):
        return False
    # Check for WHERE clause
    where_m = re.search(r"\bWHERE\b(.+?)$", stmt, re.IGNORECASE | re.DOTALL)
    if not where_m:
        return True  # no WHERE at all
    where_body = where_m.group(1)
    # Must contain id = or id IN (
    if re.search(r"\bid\s*=|\bid\s+IN\s*\(", where_body, re.IGNORECASE):
        return False  # safe — id-restricted
    return True  # WHERE exists but no id restriction


def _check_update(stmt: str) -> bool:
    """Return True (BLOCK) if UPDATE has no WHERE clause.

    A real UPDATE starts with UPDATE <table> SET. JOIN-style updates that use
    FROM ... WHERE are still safe — they have WHERE present.
    """
    if not _UPDATE.search(stmt):
        return False
    # Only flag statements that are actual UPDATE DML (not part of a view def etc.)
    if not re.match(r"\s*UPDATE\b", stmt, re.IGNORECASE):
        return False
    # Check for WHERE clause anywhere in the statement (handles FROM...WHERE joins)
    if re.search(r"\bWHERE\b", stmt, re.IGNORECASE):
        return False  # has WHERE — safe
    # Also allow if FROM clause is present (JOIN-style update is inherently restricted)
    if re.search(r"\bFROM\b", stmt, re.IGNORECASE):
        return False
    return True  # no WHERE, no FROM — blanket update


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan_file(path: Path) -> list[str]:
    """Scan a migration file for destructive operations.

    Returns a list of human-readable findings (empty = safe).
    Format: "<file>:<line>: <PATTERN>: <matched_text>"
    """
    raw = path.read_text(encoding="utf-8")

    # Collect allowlisted lines BEFORE stripping comments
    # (so we can match the `-- linter-allow:` pattern against the raw file)
    allowed_lines = _collect_allowlisted_lines(raw)

    # Strip comments first (before dollar-quote stripping, to avoid stripping
    # comment text inside dollar-quoted blocks — those are already opaque to us)
    stripped = strip_sql_comments(raw)
    stripped = strip_dollar_quotes(stripped)

    findings: list[str] = []

    # Build a line-number map on the stripped text
    lines = stripped.splitlines(keepends=True)
    line_starts: list[int] = []
    pos = 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line)

    def lineno(char_pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= char_pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1  # 1-based

    def report(char_pos: int, label: str, matched: str) -> None:
        ln = lineno(char_pos)
        # Skip findings on allowlisted lines
        if ln in allowed_lines:
            return
        snippet = matched.strip().replace("\n", " ")[:80]
        findings.append(f"{path}:{ln}: {label}: {snippet}")

    # Build safe zones on the stripped text
    safe_zones = build_safe_zones(stripped)

    # ---- Simple pattern checks ----
    simple_patterns = [
        ("DROP TABLE", _DROP_TABLE),
        ("DROP COLUMN", _DROP_COLUMN),
        ("DROP CONSTRAINT", _DROP_CONSTRAINT),
        ("DROP INDEX", _DROP_INDEX),
        ("TRUNCATE", _TRUNCATE),
    ]

    for label, pat in simple_patterns:
        for m in pat.finditer(stripped):
            if not in_safe_zone(m.start(), safe_zones):
                report(m.start(), label, m.group())

    # ---- DROP TRIGGER — only flag if not in a safe recreate zone ----
    for m in _DROP_TRIGGER.finditer(stripped):
        if not in_safe_zone(m.start(), safe_zones):
            report(m.start(), "DROP TRIGGER", m.group())

    # ---- DROP FUNCTION — flag always (CREATE OR REPLACE FUNCTION is safe;
    #      DROP FUNCTION is a separate keyword sequence) ----
    for m in _DROP_FUNCTION.finditer(stripped):
        if not in_safe_zone(m.start(), safe_zones):
            report(m.start(), "DROP FUNCTION", m.group())

    # ---- DROP VIEW — only flag if not in a safe recreate zone ----
    for m in _DROP_VIEW.finditer(stripped):
        if not in_safe_zone(m.start(), safe_zones):
            report(m.start(), "DROP VIEW", m.group())

    # ---- ALTER TYPE ... DROP VALUE ----
    for m in _ALTER_TYPE_DROP_VALUE.finditer(stripped):
        report(m.start(), "ALTER TYPE ... DROP VALUE", m.group())

    # ---- ALTER COLUMN ... DROP (DROP DEFAULT, DROP NOT NULL) ----
    # Must NOT also be a DROP COLUMN (already caught above)
    for m in _ALTER_COLUMN_DROP.finditer(stripped):
        matched = m.group()
        if not re.search(r"\bDROP\s+COLUMN\b", matched, re.IGNORECASE):
            report(m.start(), "ALTER COLUMN ... DROP", matched)

    # ---- DELETE / UPDATE: statement-level analysis ----
    for stmt_start, stmt in _split_statements(stripped):
        if _check_delete(stmt):
            # Find position of DELETE FROM inside the statement
            dm = _DELETE_FROM.search(stmt)
            pos_in_stmt = dm.start() if dm else 0
            report(stmt_start + pos_in_stmt, "DELETE without safe id-restricted WHERE", stmt.strip()[:80])
        if _check_update(stmt):
            um = _UPDATE.search(stmt)
            pos_in_stmt = um.start() if um else 0
            report(stmt_start + pos_in_stmt, "UPDATE without WHERE", stmt.strip()[:80])

    return findings


def _warn_if_supabase_db_push_context() -> None:
    """CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS M_PRIMARY: emit a loud warning
    if this script appears to be invoked from a `supabase db push` workflow.

    The CLI's shadow-diff re-applies historic CREATE OR REPLACE VIEW
    statements that pre-date current boot_briefing arms — confirmed root
    cause of the 2026-05-22 arm-loss incident. Operators must use the
    orch's direct psycopg-apply pattern instead.

    Heuristic: parent process command starts with `supabase ` (the CLI
    binary). Argv check is NOT used — migration file paths begin with
    `supabase/` and would false-positive.
    """
    import os as _os
    import subprocess as _sp

    try:
        ppid = _os.getppid()
        out = _sp.run(
            ["ps", "-p", str(ppid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, _sp.SubprocessError):
        return

    parent_cmd = (out.stdout or "").strip()
    # Match only the supabase CLI binary as the parent — exact basename check.
    first = parent_cmd.split(None, 1)[0] if parent_cmd else ""
    parent_exe = first.rsplit("/", 1)[-1]
    if parent_exe != "supabase":
        return

    sys.stderr.write(
        "\n⚠️  WARNING — invoked from supabase CLI\n"
        f"   parent: {parent_cmd[:160]}\n"
        "   Per CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS (decision 962) +\n"
        "   CLAUDE.md rule: NEVER run `supabase db push` against production\n"
        "   (project_ref ceayjeamtmcyzzvqflus). Shadow-diff re-applies historic\n"
        "   CREATE OR REPLACE VIEW statements and silently strips arms from\n"
        "   boot_briefing. Use the orch's direct psycopg-apply pattern — see\n"
        "   PR #41 / #42 / #44 migration apply scripts.\n\n"
    )


def main() -> None:
    _warn_if_supabase_db_push_context()
    if len(sys.argv) < 2:
        print(
            "Usage: check_additive_migration.py <file.sql> [<file2.sql> ...]",
            file=sys.stderr,
        )
        sys.exit(2)

    all_findings: list[str] = []
    exit_code = 0

    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            sys.exit(2)
        findings = scan_file(path)
        all_findings.extend(findings)

    if all_findings:
        exit_code = 1
        print(
            "BLOCKED — destructive operations detected. Requires cai approval before pre-apply:",
            file=sys.stderr,
        )
        for finding in all_findings:
            print(finding)
    else:
        print("OK — additive only, safe to pre-apply.")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
