#!/usr/bin/env python3
"""grader.py for task-02-manzil-miss-classifier (op#35690,
docs/loop-pilot-design-v1.md section 6a). Ground truth for
classifyManzilMiss() -- independent of whatever the spec-writer's own
acceptance tests happened to cover.

Never reachable from any role worktree (run_pilot.py never checks this out
anywhere a role operates) -- see docs/loop-pilot-design-v1.md section 4 and
the 2026-10-30 isolation fix. Only run_pilot.py (the harness) invokes this,
after both the builder and reviewer roles have already exited.

Writes a temporary vitest spec into the ALREADY-EXITED worktree (no role
will run again in it afterward), runs it, reads the result, deletes the
temp file. Exit 0 means the grader itself ran to completion -- "a bug
survived" is reported in the JSON (any_bug_survived), never via exit code.
Nonzero exit means the grader itself broke (missing npx/vitest, I/O error)
and the caller must report "could not measure", not a clean PASS/FAIL.

Usage:
    grader.py --repo-dir <path, already checked out at --head-sha> --head-sha <sha>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TASK_ID = "task-02-manzil-miss-classifier"
IMPL_REL = "web/src/modules/scheduler/manzilMissClassifier.ts"
ORACLE_REL = "web/src/modules/scheduler/__tests__/_grader_oracle_task-02-manzil-miss-classifier.test.ts"

ORACLE_TS = r"""
// Transient oracle test for task-02-manzil-miss-classifier, written by
// grader.py. Not part of the repo's real test suite -- deleted after this
// one run.
import { describe, it, expect } from "vitest";
import { classifyManzilMiss } from "../manzilMissClassifier";

describe("oracle: classifyManzilMiss", () => {
  it("a first-ever miss (empty pastMissDates) is forgiven, ordinal 1", () => {
    const r = classifyManzilMiss([], new Date("2026-03-13T00:00:00Z"));
    expect(r.ordinalInWindow).toBe(1);
    expect(r.status).toBe("forgiven");
  });

  it("a 2nd miss inside the window triggers protocol-review-notification", () => {
    const past = [new Date("2026-03-06T00:00:00Z")]; // 7 days before
    const r = classifyManzilMiss(past, new Date("2026-03-13T00:00:00Z"));
    expect(r.ordinalInWindow).toBe(2);
    expect(r.status).toBe("protocol-review-notification");
  });

  it("a 3rd miss inside the window is auto-flagged", () => {
    const past = [
      new Date("2026-03-06T00:00:00Z"),
      new Date("2026-02-27T00:00:00Z"),
    ];
    const r = classifyManzilMiss(past, new Date("2026-03-13T00:00:00Z"));
    expect(r.ordinalInWindow).toBe(3);
    expect(r.status).toBe("protocol-review-flagged");
  });

  it("a past miss exactly 27 days before counts (inclusive lower boundary)", () => {
    // newMissDate = 2026-03-13, window lower bound = 2026-02-14 (27 days back)
    const past = [new Date("2026-02-14T00:00:00Z")];
    const r = classifyManzilMiss(past, new Date("2026-03-13T00:00:00Z"));
    expect(r.ordinalInWindow).toBe(2);
    expect(r.windowStartDate).toBe("2026-02-14");
  });

  it("a past miss 28 days before does NOT count (one day outside the window)", () => {
    const past = [new Date("2026-02-13T00:00:00Z")];
    const r = classifyManzilMiss(past, new Date("2026-03-13T00:00:00Z"));
    expect(r.ordinalInWindow).toBe(1);
    expect(r.status).toBe("forgiven");
  });

  it("excludes a pastMissDates entry that is actually after newMissDate", () => {
    const past = [new Date("2026-03-20T00:00:00Z")]; // after newMissDate
    const r = classifyManzilMiss(past, new Date("2026-03-13T00:00:00Z"));
    expect(r.ordinalInWindow).toBe(1);
    expect(r.status).toBe("forgiven");
  });

  it("compares by UTC calendar day, not raw elapsed milliseconds, near the boundary", () => {
    // Calendar days: 2026-02-13 -> 2026-03-13 is exactly 28 days apart, so
    // this past miss must be EXCLUDED (one day outside the 27-day-back
    // window). But the raw millisecond gap between these two specific
    // timestamps is only ~27.08 days -- an implementation that floors
    // (newMissDate.getTime() - d.getTime()) / 86400000 and compares <= 27
    // would wrongly INCLUDE it. The spec requires calendar-day comparison
    // (drop time-of-day) specifically to rule this out.
    const past = [new Date("2026-02-13T23:00:00Z")];
    const r = classifyManzilMiss(past, new Date("2026-03-13T01:00:00Z"));
    expect(r.ordinalInWindow).toBe(1);
    expect(r.status).toBe("forgiven");
  });

  it("computes windowStartDate via calendar-day arithmetic across a month/year boundary", () => {
    const r = classifyManzilMiss([], new Date("2026-01-10T00:00:00Z"));
    expect(r.windowStartDate).toBe("2025-12-14");
  });

  it("does not mutate the input array", () => {
    // Deliberately unsorted -- an in-place .sort() mutation would be
    // invisible if the fixture were already sorted.
    const past = [
      new Date("2026-03-06T00:00:00Z"),
      new Date("2026-02-27T00:00:00Z"),
    ];
    const copy = past.map((d) => new Date(d.getTime()));
    classifyManzilMiss(past, new Date("2026-03-13T00:00:00Z"));
    expect(past.map((d) => d.getTime())).toEqual(copy.map((d) => d.getTime()));
  });
});
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-dir", required=True)
    p.add_argument("--head-sha", required=True)
    args = p.parse_args(argv)

    repo_dir = Path(args.repo_dir)
    impl_path = repo_dir / IMPL_REL
    oracle_path = repo_dir / ORACLE_REL

    bugs = []
    any_survived = False

    if not impl_path.exists():
        bugs.append({"id": "not-implemented", "survived": True,
                     "detail": f"{IMPL_REL} does not exist at {args.head_sha}"})
        any_survived = True
    else:
        oracle_path.parent.mkdir(parents=True, exist_ok=True)
        oracle_path.write_text(ORACLE_TS)
        try:
            r = subprocess.run(
                ["npx", "vitest", "run", str(oracle_path)],
                cwd=str(repo_dir), capture_output=True, text=True, timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as e:
            # The grader itself broke (npx/vitest unavailable, timeout, etc.)
            # -- distinct from a real test failure. Caller must report
            # "could not measure", never guess PASS/FAIL.
            print(f"GRADER_ERROR: could not run oracle: {e}", file=sys.stderr)
            return 2
        finally:
            oracle_path.unlink(missing_ok=True)

        passed = r.returncode == 0
        any_survived = not passed
        bugs.append({
            "id": "oracle",
            "survived": not passed,
            "detail": "oracle test suite " + ("passed" if passed else "failed") +
                      (f"\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}" if not passed else ""),
        })

    print(json.dumps({
        "task_id": TASK_ID,
        "head_sha": args.head_sha,
        "bugs": bugs,
        "any_bug_survived": any_survived,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
