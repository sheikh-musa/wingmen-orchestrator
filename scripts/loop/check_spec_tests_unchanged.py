#!/usr/bin/env python3
"""check_spec_tests_unchanged.py — the "spec-tests-unchanged" merge-gate check.

Part of the cc-loop 3-role loop (op#35690, reports/loop-pilot-design-20261029.md).
The builder role may implement, but may NOT edit the acceptance test files the
spec-writer committed. This check is the enforcement: it re-reads the manifest
AS COMMITTED AT the spec commit (never trusting whatever the manifest claims
at HEAD, so the builder can't launder an edited test file by also rewriting
the manifest's hashes), recomputes each listed test file's sha256 at the PR
head, and posts a GitHub commit status for the head sha.

Usage:
    scripts/loop/check_spec_tests_unchanged.py --repo-dir <path> \
        --task-id <id> --spec-commit-sha <sha> --head-sha <sha> \
        --owner <gh-owner> --repo <gh-repo> [--post-status]

Exits 0 if unchanged, 1 if changed or missing (regardless of --post-status),
so it also works as a plain CI gate without the GitHub status side effect.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import json

from gh_status import post_status

CONTEXT = "spec-tests-unchanged"


def git_show(repo_dir: str, rev: str, path: str) -> bytes | None:
    """Returns the blob content at rev:path, or None if it does not exist."""
    r = subprocess.run(
        ["git", "-C", repo_dir, "show", f"{rev}:{path}"],
        capture_output=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout


def manifest_at(repo_dir: str, rev: str, task_id: str) -> dict:
    raw = git_show(repo_dir, rev, f".loop/{task_id}/manifest.json")
    if raw is None:
        raise FileNotFoundError(f".loop/{task_id}/manifest.json not found at {rev}")
    return json.loads(raw)


def check(repo_dir: str, task_id: str, spec_commit_sha: str, head_sha: str) -> tuple[bool, str]:
    manifest = manifest_at(repo_dir, spec_commit_sha, task_id)
    mismatches = []
    for entry in manifest["acceptance_tests"]:
        path, expected_sha = entry["path"], entry["sha256"]
        content = git_show(repo_dir, head_sha, path)
        if content is None:
            mismatches.append(f"{path}: missing at head")
            continue
        actual_sha = hashlib.sha256(content).hexdigest()
        if actual_sha != expected_sha:
            mismatches.append(f"{path}: hash changed since spec commit {spec_commit_sha[:12]}")
    if mismatches:
        return False, "; ".join(mismatches)
    n = len(manifest["acceptance_tests"])
    return True, f"{n} acceptance test file(s) unchanged since spec commit {spec_commit_sha[:12]}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-dir", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--spec-commit-sha", required=True)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--owner", default=None)
    p.add_argument("--repo", default=None)
    p.add_argument("--post-status", action="store_true")
    args = p.parse_args(argv)

    if args.post_status and not (args.owner and args.repo):
        p.error("--post-status requires --owner and --repo")

    ok, detail = check(args.repo_dir, args.task_id, args.spec_commit_sha, args.head_sha)
    print(("PASS: " if ok else "FAIL: ") + detail)

    if args.post_status:
        post_status(args.owner, args.repo, args.head_sha,
                     "success" if ok else "failure", CONTEXT, detail)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
