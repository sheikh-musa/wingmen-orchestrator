#!/usr/bin/env python3
"""sweep_persisted_output_secrets.py — one-off remediation sweep (orch-console
#51314, bus #51269/#51285/#51290): PR#282 proved tool-results/*.txt spill files
written BEFORE the fix could hold secret-shaped values that were never scanned
or redacted. This walks every existing tool-results/* file under one or more
project roots, finds every secret-pattern match (ALL matches, not just the
first), redacts in place, and reports COUNTS per pattern class and per project
directory -- never a value, never a hash of a value in the report.

Separately, each match is hash-compared (sha256, in memory only) against a set
of CURRENT credential values pulled from known .env files, so a hit that is a
live, real, current secret can be flagged for operator escalation WITHOUT ever
printing or logging the raw value or its hash.

Usage:
    python3 -m scripts.sweep_persisted_output_secrets [--root DIR ...] [--dry-run]

--dry-run: report what WOULD be redacted, touch nothing on disk.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.hooks.secrets_output_scanner import SECRET_PATTERNS, REDACTION  # noqa: E402

DEFAULT_ROOTS = [os.path.expanduser("~/.claude/projects")]

# Known .env-style files to pull CURRENT credential values from for the hash
# compare. Read once, hashed immediately, raw values discarded from memory.
CURRENT_CREDENTIAL_SOURCES = [
    os.path.expanduser("~/wingmen/orchestrator/.env"),
    os.path.expanduser("~/wingmen/projects/ihsanos/.env.local"),
]

_ENV_LINE_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


def _current_credential_hashes() -> dict[str, str]:
    """Read known .env files, return {sha256(value): "SOURCE_FILE:VAR_NAME"}.
    Raw values are never stored past this function's local scope."""
    hashes: dict[str, str] = {}
    for src in CURRENT_CREDENTIAL_SOURCES:
        try:
            with open(src, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            m = _ENV_LINE_RE.match(line.strip())
            if not m:
                continue
            name, value = m.group(1), m.group(2).strip().strip('"').strip("'")
            if not value:
                continue
            h = hashlib.sha256(value.encode("utf-8")).hexdigest()
            hashes[h] = f"{os.path.basename(src)}:{name}"
    return hashes


def _project_dir_of(path: Path) -> str:
    """The ~/.claude/projects/<encoded-project>/ directory name a tool-results
    file lives under -- the per-project attribution unit for the report."""
    parts = path.parts
    try:
        idx = parts.index("projects")
        return parts[idx + 1]
    except (ValueError, IndexError):
        return "unknown"


def sweep_file(path: Path, current_hashes: dict[str, str], dry_run: bool) -> tuple[Counter, list[str]]:
    """Scan one file for every class's every match. Redact in place (unless
    dry_run). Returns (per-class hit counter, list of 'STORE:VAR' refs for any
    match that hash-equals a CURRENT credential)."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Counter(), []

    counts: Counter = Counter()
    real_current_refs: list[str] = []
    new_content = content
    for cls, pattern in SECRET_PATTERNS.items():
        matches = list(pattern.finditer(new_content))
        if not matches:
            continue
        counts[cls] += len(matches)
        for m in matches:
            h = hashlib.sha256(m.group(0).encode("utf-8")).hexdigest()
            if h in current_hashes:
                real_current_refs.append(current_hashes[h])
        new_content = pattern.sub(REDACTION.format(cls=cls), new_content)

    if counts and not dry_run and new_content != content:
        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError:
            pass
    return counts, real_current_refs


def sweep_roots(roots: list[str], dry_run: bool) -> dict:
    current_hashes = _current_credential_hashes()
    per_class: Counter = Counter()
    per_project: dict[str, Counter] = {}
    real_current_hits: list[tuple[str, str]] = []  # (project_dir, store_ref)
    files_scanned = 0
    files_with_hits = 0

    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for tr_dir in root_path.rglob("tool-results"):
            if not tr_dir.is_dir():
                continue
            for f in tr_dir.iterdir():
                if not f.is_file():
                    continue
                files_scanned += 1
                counts, refs = sweep_file(f, current_hashes, dry_run)
                if counts:
                    files_with_hits += 1
                    proj = _project_dir_of(f)
                    per_project.setdefault(proj, Counter()).update(counts)
                    per_class.update(counts)
                    for ref in refs:
                        real_current_hits.append((proj, ref))

    return {
        "files_scanned": files_scanned,
        "files_with_hits": files_with_hits,
        "per_class": dict(per_class),
        "per_project": {k: dict(v) for k, v in per_project.items()},
        "real_current_hits": real_current_hits,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", action="append", dest="roots", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    roots = args.roots or DEFAULT_ROOTS

    result = sweep_roots(roots, args.dry_run)
    print(f"files scanned: {result['files_scanned']}  files with hits: {result['files_with_hits']}")
    print("per-class counts:", result["per_class"])
    print("per-project counts:")
    for proj, counts in sorted(result["per_project"].items()):
        print(f"  {proj}: {counts}")
    if result["real_current_hits"]:
        print("\n!!! REAL CURRENT CREDENTIAL MATCHES (store:var refs only, no values) !!!")
        for proj, ref in result["real_current_hits"]:
            print(f"  project={proj} ref={ref}")
    else:
        print("\nno match hash-equaled a current credential value")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
