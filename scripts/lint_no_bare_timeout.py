#!/usr/bin/env python3
"""lint_no_bare_timeout.py — reject bare `timeout N ...` in committed fleet tooling.

FIRST ITEM IN THE HoQ DETERMINISTIC FLOOR (CAI-RESP-554, 2026-07-25).

WHY THIS IS A GATE AND NOT A NOTE: macOS ships no `timeout` binary (coreutils provides
`gtimeout`). A committed script using it is silently broken on BOTH fleet hosts — and it fails
in the worst possible way. `timeout 25 git ls-remote origin` doesn't error usefully; the shell
reports "command not found" and the wrapped command NEVER RUNS, so the caller sees empty output
and consumes that emptiness as a measurement. That is precisely how cai published "ls-remote
returns ZERO refs" as the factual basis of a filed decision, when the real answer was 17.

It bit two bodies independently within hours (Nazim's first ssh to the Studio died on it and he
forgot; cai never knew and built a false claim on it). A defect that recurs on goodwill and stops
dead with a one-line mechanical check belongs in the floor, not in a lesson.

It BLOCKS from day one rather than starting advisory (CAI-552 Q1): a pure string match with a
known-correct alternative has ~0 false positives, so it cannot manufacture the wolf-crying that
trains people to route around a gate.

Correct alternatives:
    gtimeout 25 cmd ...           # coreutils, if installed
    ssh -o ConnectTimeout=10 ...  # protocol-level timeouts are better than wrappers
    subprocess.run(..., timeout=25)   # in Python, always
    perl -e 'alarm N; exec @ARGV' N cmd ...   # last resort, no extra deps

Usage:
    scripts/lint_no_bare_timeout.py [paths...]     # default: git-tracked shell + python
    exit 0 = clean, exit 1 = violations found (CI-blocking)
"""
from __future__ import annotations

import re
import subprocess
import sys

# `timeout` as a COMMAND: start of line / after a pipe, semicolon, &&, ||, $( or backtick,
# followed by a duration-looking argument. Deliberately does NOT match `timeout=30`,
# `--timeout 30`, `connect_timeout`, or the word inside a comment or string assignment.
PATTERN = re.compile(r'(?:^|[;&|(]|\$\(|`|\bthen\b|\bdo\b|\belse\b)\s*timeout\s+-?\d')
ALLOW_MARKER = "lint-allow-bare-timeout"   # explicit, greppable, needs a reason beside it


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.sh", "*.py", "*.bash", "*.zsh"],
                         capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f]


def scan(paths: list[str]) -> list[tuple[str, int, str]]:
    hits = []
    for path in paths:
        if path.endswith("lint_no_bare_timeout.py"):
            continue                      # this file documents the pattern it forbids
        try:
            lines = open(path, errors="ignore").read().splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or ALLOW_MARKER in line:
                continue
            if PATTERN.search(line):
                hits.append((path, n, stripped[:120]))
    return hits


def main() -> int:
    paths = sys.argv[1:] or tracked_files()
    hits = scan(paths)
    if not hits:
        print(f"no-bare-timeout: PASS ({len(paths)} files scanned)")
        return 0
    print(f"no-bare-timeout: FAIL — {len(hits)} violation(s)\n", file=sys.stderr)
    for path, n, line in hits:
        print(f"  {path}:{n}: {line}", file=sys.stderr)
    print("\n`timeout` does not exist on macOS — the wrapped command NEVER RUNS and its empty\n"
          "output is indistinguishable from a real result. Use `gtimeout`, a protocol-level\n"
          "timeout (ssh -o ConnectTimeout), or subprocess.run(timeout=). If a hit is genuinely\n"
          f"intentional, mark the line with `{ALLOW_MARKER}` and say why.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
