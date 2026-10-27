#!/usr/bin/env python3
"""agent_messages_writer_gate — enforce-in-code gate for backlog#124's final
step (orch-console ruling, bus #74885/#74906 part 4).

Fails on any raw `INSERT INTO agent_messages` found in production code
outside the sanctioned helper (bus_send.py's send(), which posts through
post_agent_message -- PR #490). This is migration 124's release gate: console
releases the revoke ONLY when this reports zero bypasses fleet-wide. It is a
gate, not a one-time audit, so it must run in CI (and pre-push) and stay green
as new scripts are added -- a report can go stale the day after it's written;
this can't.

Scans scripts/ and nervous_system/ (where the ~50 original violators live,
bus #74880/#74896) -- NOT tests/, since test fixtures legitimately build their
own throwaway agent_messages tables against an isolated ephemeral PG, which is
not a production write path.

Usage:
  scripts/agent_messages_writer_gate.py [root ...]   # defaults to repo root

Exit codes:
  0 — no raw INSERT INTO agent_messages found outside tests/
  1 — at least one bypass found (block pre-push / fail CI)
  2 — usage error
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCAN_DIRS = ("scripts", "nervous_system", "ops")
EXCLUDE_DIR_NAMES = {"tests", ".venv", "__pycache__"}
# The gate's own source necessarily contains the literal phrase it's looking
# for (its regex pattern, its docstrings) -- self-matching is a known false
# positive for this class of linter, same as check_additive_migration.py
# never flags its own DROP/REVOKE-mentioning docstrings as destructive SQL.
EXCLUDE_FILE_NAMES = {"agent_messages_writer_gate.py"}

_INSERT_RE = re.compile(r"INSERT\s+INTO\s+agent_messages", re.IGNORECASE)
_ALLOW_COMMENT_RE = re.compile(r"^\s*#\s*writer-gate-allow:\s*\S", re.IGNORECASE)
_TRIPLE_QUOTE_RE = re.compile(r'("""|\'\'\')(.*?)\1', re.DOTALL)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int


def _strip_comments(source: str) -> str:
    """Blank out '#' line comments so a comment mentioning the phrase never
    counts as a finding. Line-oriented, not a full tokenizer -- a '#' inside
    a string literal is a known, accepted false-negative risk (same class of
    simplification check_additive_migration.py's SQL comment stripper makes)."""
    out_lines = []
    for line in source.splitlines():
        idx = line.find("#")
        out_lines.append(line[:idx] if idx != -1 else line)
    return "\n".join(out_lines)


def _strip_docstrings(source: str) -> str:
    """Blank out triple-quoted blocks that are actually DOCSTRINGS (prose
    explaining the sanctioned path can legitimately contain the phrase it's
    warning against -- real false positives against bus_send.py and
    monthly_burn_snapshot.py), while leaving a triple-quoted SQL literal
    (e.g. `cur.execute(\"\"\"insert into agent_messages ...\"\"\")`, the real
    shape found in nervous_system/lane_watchdog.py) intact so it still gets
    caught.

    Heuristic, not a real parser: a triple-quoted block counts as a
    docstring only when the nearest preceding non-whitespace character is
    ':' (ends a def/class/module-level block) or it is the very first thing
    in the file -- the two positions Python docstrings can occupy. A string
    passed as a call argument (preceded by '(' or ',') is never blanked."""
    _shebang_only_re = re.compile(r"^(#!.*)?$")

    def repl(match: re.Match) -> str:
        before = source[: match.start()].rstrip()
        # A module docstring can be preceded by nothing but a shebang line
        # (real shape: scripts/bus_send.py starts '#!/usr/bin/env python3'),
        # possibly with leading blank lines before it -- .strip() both ends
        # for this check since only the CONTENT matters, not surrounding
        # whitespace.
        if _shebang_only_re.match(before.strip()) or before.endswith(":"):
            # Blank to spaces but keep embedded newlines, so line numbers for
            # anything after a multi-line docstring stay accurate.
            return "".join(c if c == "\n" else " " for c in match.group(0))
        return match.group(0)

    return _TRIPLE_QUOTE_RE.sub(repl, source)


def _allowed_lines(source: str) -> set[int]:
    allowed = set()
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if _ALLOW_COMMENT_RE.match(line):
            allowed.add(i + 2)  # the line immediately after, 1-indexed
    return allowed


def _scan_file(path: Path) -> list[Finding]:
    text = path.read_text()
    allowed = _allowed_lines(text)
    # Docstrings first: this codebase's prose is full of bus-message refs
    # shaped like '#43651', which the line-based comment stripper below would
    # mistake for a '#' comment start, truncating the line and eating the
    # docstring's own closing triple-quote before docstring-stripping ever
    # runs (real bug, caught by the docstring test against bus_send.py's
    # actual content -- not a hypothetical).
    stripped = _strip_comments(_strip_docstrings(text))

    findings: list[Finding] = []
    for match in _INSERT_RE.finditer(stripped):
        line = stripped.count("\n", 0, match.start()) + 1
        if line in allowed:
            continue
        findings.append(Finding(path=path, line=line))
    return findings


def scan(root: Path) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    for dirname in SCAN_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if EXCLUDE_DIR_NAMES & set(path.relative_to(root).parts):
                continue
            if path.name in EXCLUDE_FILE_NAMES:
                continue
            findings.extend(_scan_file(path))
    return findings


def load_baseline(path: Path) -> set[str]:
    """Pre-existing, not-yet-migrated writers (backlog#124), same shape and
    contract as tests/ci_known_failing.txt: one relative path per line,
    '#'-comments and blank lines ignored. This list must only ever SHRINK --
    when a script is migrated off the raw INSERT, delete its line in the
    same PR so the gate starts enforcing it again."""
    entries = set()
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", help="Directories to scan (default: repo root)")
    parser.add_argument("--baseline", type=Path, default=None,
                         help="Path to a known-pre-existing-violators list (one relative path "
                              "per line) that does not fail the build -- mirrors "
                              "tests/ci_known_failing.txt. Shrink-only: delete a line as its "
                              "script is migrated.")
    parser.add_argument("--require-complete", action="store_true",
                         help="Fail on ANY finding, including baseline-listed ones -- the "
                              "actual release check for migration 124 (console's ruling, bus "
                              "#74885/#74906): not 'nothing new', but 'truly zero, fleet-wide'.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    roots = [Path(r) for r in args.roots] if args.roots else [Path(__file__).resolve().parent.parent]
    baseline = load_baseline(args.baseline) if args.baseline else set()

    new_findings: list[Finding] = []
    known_findings: list[Finding] = []
    for root in roots:
        for finding in scan(root):
            rel = str(finding.path.relative_to(root))
            (known_findings if rel in baseline else new_findings).append(finding)

    if known_findings:
        print(f"agent_messages_writer_gate: {len(known_findings)} known pre-existing "
              f"bypass(es) (on the baseline, tracked for migration under backlog#124):")
        for f in known_findings:
            print(f"  {f.path}:{f.line}")

    if not new_findings:
        if not known_findings:
            print("agent_messages_writer_gate: clean -- no raw INSERT INTO agent_messages "
                  "found outside the sanctioned helper (bus_send.py's send()).")
        if args.require_complete and known_findings:
            print("agent_messages_writer_gate: --require-complete set and the baseline is "
                  "non-empty -- migration 124 does not release until this is zero.")
            return 1
        return 0

    print(f"agent_messages_writer_gate: {len(new_findings)} NEW bypass(es) found -- "
          f"route through scripts/bus_send.py's send() instead:")
    for f in new_findings:
        print(f"  {f.path}:{f.line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
