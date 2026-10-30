#!/usr/bin/env python3
"""gh_status.py — post one GitHub commit status via `gh api`.

Shared by check_spec_tests_unchanged.py, reviewer_verdict.py and
local_ci_runner.sh (the three cc-loop merge-gate checks, op#35690) so there is
exactly one place that shells out to `gh api .../statuses/:sha` — hifz's
GitHub Actions budget is exhausted, so these checks post local-runner-sourced
statuses instead of Actions check runs (docs/loop-pilot-design-v1.md
section 3).
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def post_status(owner: str, repo: str, sha: str, state: str, context: str,
                 description: str) -> None:
    if state not in ("success", "failure", "error", "pending"):
        raise ValueError(f"invalid state: {state}")
    # gh api -f fields are sent as form-encoded strings; truncate description
    # to GitHub's 140-char limit so the call can't fail on an oversized field.
    description = description[:140]
    subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/statuses/{sha}",
         "-f", f"state={state}",
         "-f", f"context={context}",
         "-f", f"description={description}"],
        check=True,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--state", required=True, choices=["success", "failure", "error", "pending"])
    p.add_argument("--context", required=True)
    p.add_argument("--description", required=True)
    args = p.parse_args(argv)
    post_status(args.owner, args.repo, args.sha, args.state, args.context, args.description)
    print(f"posted {args.context}={args.state} for {args.sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
