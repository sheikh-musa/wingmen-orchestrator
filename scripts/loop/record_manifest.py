#!/usr/bin/env python3
"""record_manifest.py — write .loop/<task_id>/manifest.json for the spec-writer role.

Part of the cc-loop 3-role loop (op#35690, docs/loop-pilot-design-v1.md).
Called by the spec-writer ROLE SESSION after it has written the spec +
acceptance test files, BEFORE it commits -- that first call omits
--spec-writer-model (the role session has no reliable way to know its own
backing model id: launch_dangerous_cc.sh's env allowlist,
scripts/lib/lane_env_allowlist.sh, does not pass MODEL/RESOLVED_MODEL into
the claude process env, by design -- see docs/loop-pilot-design-v1.md
section 1 addendum). The HARNESS (run_pilot.py), which chose the model and
therefore knows it for certain, makes a second --spec-commit-sha +
--spec-writer-model call after the commit exists to patch both fields in
place with authoritative values.

Does not commit -- the caller's own commit must include the manifest file
alongside the spec + test files, so the manifest and the hashes it claims
land in the same commit.

Usage:
    scripts/loop/record_manifest.py --task-id <id> --repo-dir <path> \
        --test-file <path> [--test-file <path> ...] \
        [--spec-writer-model <model-id>] [--spec-commit-sha <sha>]

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
    p.add_argument("--test-file", dest="test_files", action="append", default=None,
                    help="repeatable; path relative to --repo-dir. Required "
                         "for the initial (role-session) write; omitted for "
                         "the harness's later --spec-commit-sha patch call.")
    p.add_argument("--spec-writer-model", default="unknown",
                    help="the role session omits this (it can't know its own "
                         "backing model); the harness's patch call supplies it")
    p.add_argument("--spec-commit-sha", default=None,
                    help="omit at first write (filled in after the commit exists); "
                         "pass it on a follow-up call to patch the field(s) in place")
    args = p.parse_args(argv)

    out_path = manifest_path(args.repo_dir, args.task_id)

    if args.spec_commit_sha and os.path.exists(out_path):
        # Patch-in-place: keep the recorded hashes, fill spec_commit_sha and
        # (if given) the authoritative spec_writer_model from the harness.
        with open(out_path) as f:
            manifest = json.load(f)
        manifest["spec_commit_sha"] = args.spec_commit_sha
        if args.spec_writer_model != "unknown":
            manifest["spec_writer_model"] = args.spec_writer_model
    else:
        if not args.test_files:
            p.error("--test-file is required for the initial write "
                     "(no existing manifest found to patch at %r)" % out_path)
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
