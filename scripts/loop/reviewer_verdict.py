#!/usr/bin/env python3
"""reviewer_verdict.py — write .loop/<task_id>/reviews/<head_sha>.json.

Part of the cc-loop 3-role loop (op#35690, docs/loop-pilot-design-v1.md).
Called by the reviewer role AFTER it has reviewed the diff and run the tests
for an exact PR head sha. Mirrors record_manifest.py: writes the file,
does not commit, does not post the GitHub status (see gh_status.py / the
"posting the status" note below) -- one script, one job.

The verdict is keyed by head_sha and written on the shared `loop-pilot/reviews`
branch, NOT on the PR branch itself -- committing it onto the PR branch would
create a new head sha, so the verdict would describe a commit other than the
one it lives at. The reviewer role: (1) reviews/tests the PR branch at its
real head sha in its own worktree, (2) `git checkout`s (or creates)
`loop-pilot/reviews` in that same worktree, (3) runs this script, commits,
pushes, (4) posts the GitHub status for the ORIGINAL PR head sha (captured in
step 1, before switching branches) via gh_status.py.

Like record_manifest.py's --spec-writer-model, --reviewer-model is optional:
the role session can't reliably self-report its own backing model, so the
harness patches it in afterward from what it actually launched.

Usage:
    scripts/loop/reviewer_verdict.py --task-id <id> --repo-dir <path> \
        --head-sha <sha> --verdict PASS|FAIL --reason "<one line>" \
        [--reviewer-model <model-id>] [--reviewer-identity <agent-id>]
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone


def verdict_path(repo_dir: str, task_id: str, head_sha: str) -> str:
    return os.path.join(repo_dir, ".loop", task_id, "reviews", f"{head_sha}.json")


def build_verdict(task_id: str, head_sha: str, verdict: str, reason: str,
                   reviewer_model: str, reviewer_identity: str) -> dict:
    return {
        "task_id": task_id,
        "head_sha": head_sha,
        "verdict": verdict,
        "reason": reason,
        "reviewer_model": reviewer_model,
        "reviewer_identity": reviewer_identity,
        "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task-id", required=True)
    p.add_argument("--repo-dir", required=True)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--verdict", required=True, choices=["PASS", "FAIL"])
    p.add_argument("--reason", required=True)
    p.add_argument("--reviewer-model", default="unknown",
                    help="the role session omits this; the harness patches "
                         "it in afterward (same reasoning as "
                         "record_manifest.py --spec-writer-model)")
    p.add_argument("--reviewer-identity", default="unknown")
    args = p.parse_args(argv)

    out_path = verdict_path(args.repo_dir, args.task_id, args.head_sha)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if os.path.exists(out_path) and args.reviewer_model != "unknown":
        # Harness patch call: keep the role's verdict/reason, fill the model.
        with open(out_path) as f:
            doc = json.load(f)
        doc["reviewer_model"] = args.reviewer_model
        if args.reviewer_identity != "unknown":
            doc["reviewer_identity"] = args.reviewer_identity
    else:
        doc = build_verdict(args.task_id, args.head_sha, args.verdict, args.reason,
                             args.reviewer_model, args.reviewer_identity)

    with open(out_path, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")

    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
