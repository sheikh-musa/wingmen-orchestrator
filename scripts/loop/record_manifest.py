#!/usr/bin/env python3
"""record_manifest.py — write .loop/<task_id>/manifest.json for the spec-writer role.

Part of the cc-loop 3-role loop (op#35690, reports/loop-pilot-design-20261029.md).
Called by the spec-writer role AFTER it has written the spec + acceptance test
files, BEFORE it commits. The manifest's hashes become the ground truth that
check_spec_tests_unchanged.py compares the PR head against.

Does not commit -- the caller's own commit must include the manifest file
alongside the spec + test files, so the manifest and the hashes it claims
land in the same commit (spec_commit_sha is filled in by the caller after
`git commit`, via --spec-commit-sha on a second invocation, or left null and
patched by the caller script directly -- see --spec-commit-sha below).

Usage:
    scripts/loop/record_manifest.py --task-id <id> --repo-dir <path> \
        --test-file <path> [--test-file <path> ...] \
        --spec-writer-model <model-id> \
        [--spec-commit-sha <sha>]

Paths are relative to --repo-dir (and relative to --repo-dir in the written
manifest, since that's what check_spec_tests_unchanged.py re-resolves via
`git show <sha>:<path>`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone


def sha256_of(repo_dir: str, rel_path: str) -> str:
    abs_path = os.path.join(repo_dir, rel_path)
    with open(abs_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build_manifest(task_id: str, repo_dir: str, test_files: list[str],
                    spec_writer_model: str, spec_commit_sha: str | None) -> dict:
    return {
        "task_id": task_id,
        "spec_commit_sha": spec_commit_sha,
        "spec_writer_model": spec_writer_model,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "acceptance_tests": [
            {"path": p, "sha256": sha256_of(repo_dir, p)} for p in test_files
        ],
    }


def manifest_path(repo_dir: str, task_id: str) -> str:
    return os.path.join(repo_dir, ".loop", task_id, "manifest.json")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task-id", required=True)
    p.add_argument("--repo-dir", required=True)
    p.add_argument("--test-file", dest="test_files", action="append", required=True,
                    help="repeatable; path relative to --repo-dir")
    p.add_argument("--spec-writer-model", required=True)
    p.add_argument("--spec-commit-sha", default=None,
                    help="omit at first write (filled in after the commit exists); "
                         "pass it on a follow-up call to patch the field in place")
    args = p.parse_args(argv)

    out_path = manifest_path(args.repo_dir, args.task_id)

    if args.spec_commit_sha and os.path.exists(out_path):
        # Patch-in-place: keep the recorded hashes, only fill spec_commit_sha.
        with open(out_path) as f:
            manifest = json.load(f)
        manifest["spec_commit_sha"] = args.spec_commit_sha
    else:
        manifest = build_manifest(args.task_id, args.repo_dir, args.test_files,
                                   args.spec_writer_model, args.spec_commit_sha)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")

    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
