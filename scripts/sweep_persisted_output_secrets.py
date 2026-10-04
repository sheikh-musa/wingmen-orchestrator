#!/usr/bin/env python3
"""sweep_persisted_output_secrets.py — one-off remediation sweep (orch-console
#51314/#51334/#51341, bus #51269/#51285/#51290): PR#282 proved tool-results/*.txt
spill files written BEFORE the fix could hold secret-shaped values that were
never scanned or redacted. This walks every existing tool-results/* file under
one or more project roots, finds every secret-pattern match (ALL matches, not
just the first), hash-compares each one against a WIDE current-credential set
BEFORE redacting (hash-compare first is deliberate: redaction is irreversible,
so any attribution must happen before the raw value is gone -- bus #51338, a
real gap hit this session where redacting first meant a later widen-the-
credential-set ask could no longer be answered), then redacts in place.

Reports COUNTS per pattern class and per project directory -- never a value.
Each (class, file, matched_var_name|none) triple is also appended to the same
durable event log the live hook writes (secrets_output_scanner_events.log,
mode 600) with source="sweep", so a sweep run leaves the same kind of
after-the-fact-traceable record the live hook now does -- never the value or
a hash of it in that log either.

Usage:
    python3 -m scripts.sweep_persisted_output_secrets [--root DIR ...] [--dry-run]

--dry-run: report what WOULD be redacted, touch nothing on disk, still logs
nothing (a dry run must have zero side effects).
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
from scripts.hooks.secrets_output_scanner import EVENT_LOG_PATH, SECRET_PATTERNS, REDACTION  # noqa: E402

DEFAULT_ROOTS = [os.path.expanduser("~/.claude/projects")]

# bus #51341 GO: the WIDE credential set -- every .env* file under these two
# roots, not just the orchestrator's own canonical .env. Globbed once per run;
# ~322 files / ~8s on the Mini at the time this was written, acceptable for a
# one-off sweep.
CREDENTIAL_GLOB_ROOTS = [os.path.expanduser("~/wingmen"), os.path.expanduser("~/.wingmen")]

_ENV_LINE_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


def _iter_env_files():
    for root in CREDENTIAL_GLOB_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for p in root_path.rglob(".env*"):
            if p.is_file() and not p.is_symlink():
                yield p


def _current_credential_hashes() -> tuple[dict[str, str], int]:
    """Read every .env* file under the WIDE roots, return
    ({sha256(matched_span): "relative/path:VAR_NAME"}, files_checked). Raw
    values never leave this function's local scope -- only the hash and a
    (path, name) label persist. files_checked is returned here (not via a
    second glob) since enumerating ~322 files takes several seconds on its own.

    Hashes the SECRET-PATTERN MATCH SPAN within each value, not the whole raw
    value -- some patterns deliberately don't match a value end-to-end (e.g.
    postgres-dsn stops at "@", never including host/port/db), so a sweep hit's
    hash (always computed from a regex match span) would otherwise NEVER
    equal a same-credential env value's hash. A value with no pattern match at
    all (most app config, not secret-shaped) is skipped -- nothing to compare."""
    hashes: dict[str, str] = {}
    files_checked = 0
    for src in _iter_env_files():
        files_checked += 1
        try:
            lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _ENV_LINE_RE.match(line.strip())
            if not m:
                continue
            name, value = m.group(1), m.group(2).strip().strip('"').strip("'")
            if not value:
                continue
            for pattern in SECRET_PATTERNS.values():
                pm = pattern.search(value)
                if not pm:
                    continue
                h = hashlib.sha256(pm.group(0).encode("utf-8")).hexdigest()
                # first writer wins for a given hash -- fine, a hash collision across
                # distinct current secrets would mean they're the SAME value anyway.
                hashes.setdefault(h, f"{src}:{name}")
    return hashes, files_checked


def _log_sweep_event(cls: str, file: str, matched_var_name: str | None) -> None:
    """Append {ts, source:"sweep", cls, file, matched_var_name} to the same
    durable log the live hook writes -- NEVER the value or a hash of it.
    matched_var_name is None when the match didn't hash-equal any current
    credential (the common case: an old/rotated/never-current value)."""
    import datetime
    import json
    import stat

    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "sweep",
        "cls": cls,
        "file": file,
        "matched_var_name": matched_var_name,
    }
    try:
        log_dir = os.path.dirname(EVENT_LOG_PATH)
        os.makedirs(log_dir, exist_ok=True)
        is_new = not os.path.exists(EVENT_LOG_PATH)
        with open(EVENT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        if is_new:
            os.chmod(EVENT_LOG_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 600
    except OSError:
        pass  # logging must never crash the sweep


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
    """Scan one file for every class's every match. For each match: hash-compare
    against current_hashes FIRST (while the raw value still exists), then redact
    in place (unless dry_run). Returns (per-class hit counter, list of
    'path:VAR' refs for any match that hash-equals a current credential)."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
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
            ref = current_hashes.get(h)
            if ref:
                real_current_refs.append(ref)
            if not dry_run:
                _log_sweep_event(cls, str(path), ref)
        new_content = pattern.sub(REDACTION.format(cls=cls), new_content)

    if counts and not dry_run and new_content != content:
        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError:
            pass
    return counts, real_current_refs


def sweep_roots(roots: list[str], dry_run: bool) -> dict:
    current_hashes, credential_files_checked = _current_credential_hashes()
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
        "credential_files_checked": credential_files_checked,
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
    print(f"credential files checked (wide set): {result['credential_files_checked']}")
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
    if not args.dry_run:
        print(f"\nevent log: {EVENT_LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
