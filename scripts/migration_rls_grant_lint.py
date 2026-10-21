#!/usr/bin/env python3
"""migration_rls_grant_lint.py — STATIC, no-DB migration-diff lint for the
RLS-UPDATE-without-column-GRANT gap + non-terminal lifecycle value-adds (PB-03,
build-rigor track op#32734/32735, orch-console gate #73186/#73191).

COMPLEMENT, NOT a duplicate, of scripts/rls_grant_lint.py:
  * rls_grant_lint.py is a LIVE-DB posture net (CAI-RESP-511): it fails on the
    anon-reachable write/TRUNCATE/RLS-off surface of the CURRENT substrate.
  * THIS lint is a STATIC authoring-time net over a migration's added SQL text:
    it catches, at PR time and for ANY silo's migrations, the one gap the live net
    cannot see — an RLS UPDATE/ALL policy that is NOT bounded by a column-level
    GRANT (so "they can only write field X" is false — an RLS policy does not
    restrict COLUMNS; you need a column GRANT or a SECDEF write path;
    ihsanos PR#1102, cosem PR#429) — plus a non-terminal lifecycle value-add that
    exposes every pre-existing reader (cosem PR#429).

WHY A WARN-LINT, NOT A FAIL-CLOSED PRE-TOOL HOOK (orch-console Condition 2): whether
the *corresponding* column-GRANT exists can depend on another migration or the live
schema — context a single pre-tool call lacks — so hard-blocking would false-positive
and wedge the fleet. This emits a LOUD warn (GitHub-Actions ::warning:: annotation on
the PR) and exits 0 by default; --strict makes it exit 1 for CI gating by choice.

Stdlib only. No network, no DB. Pure text analysis of the SQL you give it.

Usage:
  migration_rls_grant_lint.py <file.sql> [<file.sql> ...]   # lint these files
  migration_rls_grant_lint.py --diff [<base-ref>]           # lint added lines in
                                 the git diff vs <base-ref> (default origin/HEAD)
  flags: --github  emit ::warning file=...:: annotations (auto-on under GITHUB_ACTIONS)
         --strict  exit 1 if any finding (default: WARN, exit 0)
         --json    machine-readable findings
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict

# ── SQL text normalization ───────────────────────────────────────────────────
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(sql: str) -> str:
    """Drop -- line comments and /* */ block comments so a DDL keyword inside a
    comment never trips a match. (Dollar-quoted bodies are left intact — policies
    and grants never live inside them, and over-matching a WARN is harmless.)"""
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))


def _norm_table(raw: str) -> str:
    """Normalize a (possibly schema-qualified, possibly quoted) table ref to a bare
    lowercased identifier for correlation: public."Foo" -> foo."""
    last = raw.strip().split(".")[-1]
    return last.strip().strip('"').lower()


# ── matchers ─────────────────────────────────────────────────────────────────
# CREATE|ALTER POLICY <name> ON <table>   (captures the table; the FOR clause is
# read from the window up to the next ';').
_POLICY_RE = re.compile(
    r"(?is)\b(?:CREATE|ALTER)\s+POLICY\s+[\w\"]+\s+ON\s+((?:[\w\"]+\.)?[\w\"]+)"
)
# A COLUMN-level grant is the privilege list followed by a (col, col) list, ON a table.
# e.g. GRANT UPDATE (answers, saved_index) ON theory_attempts TO authenticated;
_COLUMN_GRANT_RE = re.compile(
    r"(?is)\bGRANT\s+[^;]*?\([^)]*\)\s+ON\s+(?:TABLE\s+)?((?:[\w\"]+\.)?[\w\"]+)"
)
# non-terminal lifecycle signals
_ENUM_ADD_RE = re.compile(r"(?is)\bALTER\s+TYPE\s+((?:[\w\"]+\.)?[\w\"]+)\s+ADD\s+VALUE")
_NONTERMINAL_DEFAULT_RE = re.compile(
    r"(?is)\bDEFAULT\s+'(in_progress|draft|pending|open|active)'"
)

_FOR_RE = re.compile(r"(?is)\bFOR\s+(ALL|SELECT|INSERT|UPDATE|DELETE)\b")
# update-capable = FOR UPDATE, FOR ALL, or NO for-clause (CREATE POLICY defaults to ALL).
_UPDATE_CAPABLE = {"ALL", "UPDATE"}


@dataclass
class Finding:
    code: str
    obj: str
    detail: str
    file: str = ""


def _for_commands_after(text: str, start: int) -> set[str]:
    """The set of FOR-commands in the policy statement starting at `start`
    (window = up to the next ';' or end). Empty set => no FOR clause => defaults ALL."""
    end = text.find(";", start)
    window = text[start : end if end != -1 else len(text)]
    return {m.group(1).upper() for m in _FOR_RE.finditer(window)}


def scan_sql(sql: str, file: str = "") -> list[Finding]:
    """PURE. Return findings for one SQL blob. No DB, no IO."""
    text = _strip_comments(sql)
    findings: list[Finding] = []

    col_granted = {_norm_table(m.group(1)) for m in _COLUMN_GRANT_RE.finditer(text)}

    for m in _POLICY_RE.finditer(text):
        table = _norm_table(m.group(1))
        cmds = _for_commands_after(text, m.start())
        update_capable = (not cmds) or bool(cmds & _UPDATE_CAPABLE)
        if not update_capable:
            continue
        if table not in col_granted:
            how = "FOR " + "/".join(sorted(cmds)) if cmds else "no FOR clause (defaults to ALL)"
            findings.append(Finding(
                "RLS_UPDATE_NO_COLUMN_GRANT", table,
                f"policy on `{table}` is UPDATE-capable ({how}) but this diff adds no "
                f"column-level GRANT (...cols...) ON {table}. An RLS policy does NOT "
                f"restrict COLUMNS — confirm a column GRANT or a SECDEF write path, or a "
                f"negative-control proof (direct PATCH of a barred column -> 42501). PB-03.",
                file,
            ))

    for m in _ENUM_ADD_RE.finditer(text):
        t = _norm_table(m.group(1))
        findings.append(Finding(
            "LIFECYCLE_VALUE_ADD", t,
            f"enum `{t}` gains a value — sweep ALL readers of every table using it; a "
            f"once-terminal column now carries transient rows (PB-03 step 4).", file,
        ))
    for m in _NONTERMINAL_DEFAULT_RE.finditer(text):
        findings.append(Finding(
            "LIFECYCLE_NONTERMINAL_DEFAULT", m.group(1),
            f"a non-terminal default '{m.group(1)}' is introduced — sweep ALL "
            f"results/lock/aggregate readers of the table for a terminal-state filter "
            f"(PB-03 step 4).", file,
        ))
    return findings


# ── IO / CLI ─────────────────────────────────────────────────────────────────
def _added_lines_from_diff(base: str) -> str:
    """The added (+) lines of `git diff base...HEAD -- migrations/`, as one blob."""
    try:
        out = subprocess.run(
            ["git", "diff", f"{base}...HEAD", "--", "migrations/"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"migration_rls_grant_lint: could not read git diff ({e!r}) — could not "
              f"measure, not a finding", file=sys.stderr)
        return ""
    added = [ln[1:] for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    return "\n".join(added)


def _emit_github(findings: list[Finding]) -> None:
    for f in findings:
        loc = f"file={f.file}," if f.file else ""
        print(f"::warning {loc}title=PB-03 {f.code}::{f.detail}")


def main(argv: list[str]) -> int:
    args = argv[1:]
    strict = "--strict" in args
    as_json = "--json" in args
    github = "--github" in args or os.environ.get("GITHUB_ACTIONS") == "true"
    rest = [a for a in args if not a.startswith("--")]

    findings: list[Finding] = []
    if "--diff" in args:
        base = rest[0] if rest else "origin/HEAD"
        findings = scan_sql(_added_lines_from_diff(base), file="(diff)")
    elif rest:
        for path in rest:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    findings += scan_sql(fh.read(), file=path)
            except OSError as e:
                print(f"migration_rls_grant_lint: cannot read {path}: {e}", file=sys.stderr)
                return 2
    else:
        print(__doc__.split("Usage:")[1], file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        print(f"== migration RLS/grant lint (PB-03) — {len(findings)} WARN ==")
        for f in findings:
            print(f"  warn [{f.code}] {f.obj}: {f.detail}" + (f"  ({f.file})" if f.file else ""))
        if not findings:
            print("  clean — no RLS-UPDATE-without-column-GRANT or lifecycle-value-add in this diff.")
    if github:
        _emit_github(findings)

    # WARN classification: advisory. Exit 0 unless --strict (orch-console #73191).
    return 1 if (findings and strict) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
