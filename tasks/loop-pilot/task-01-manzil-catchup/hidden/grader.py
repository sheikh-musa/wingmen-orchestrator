#!/usr/bin/env python3
"""grader.py for task-01-manzil-catchup (op#35690, docs/loop-pilot-design-v1.md
section 6a). Ground truth for distributeCatchUpFlush() -- independent of
whatever the spec-writer's own acceptance tests happened to cover.

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

TASK_ID = "task-01-manzil-catchup"
IMPL_REL = "web/src/modules/scheduler/manzilCatchUp.ts"
ORACLE_REL = "web/src/modules/scheduler/__tests__/_grader_oracle_task-01-manzil-catchup.test.ts"

ORACLE_TS = r"""
// Transient oracle test for task-01-manzil-catchup, written by grader.py.
// Not part of the repo's real test suite -- deleted after this one run.
import { describe, it, expect } from "vitest";
import { distributeCatchUpFlush } from "../manzilCatchUp";

describe("oracle: distributeCatchUpFlush", () => {
  it("round-robins on index % 3, preserving per-day order", () => {
    const ids = ["a", "b", "c", "d", "e", "f", "g"];
    const r = distributeCatchUpFlush(ids, new Date("2026-03-10T00:00:00Z"));
    expect(r.day1).toEqual(["a", "d", "g"]);
    expect(r.day2).toEqual(["b", "e"]);
    expect(r.day3).toEqual(["c", "f"]);
  });

  it("computes calendar-day dates, not 24h multiples, across a month boundary", () => {
    const r = distributeCatchUpFlush(["x"], new Date("2026-01-30T00:00:00Z"));
    expect(r.day1Date).toBe("2026-01-31");
    expect(r.day2Date).toBe("2026-02-01");
    expect(r.day3Date).toBe("2026-02-02");
  });

  it("handles a plain same-month case", () => {
    const r = distributeCatchUpFlush(["x"], new Date("2026-06-04T00:00:00Z"));
    expect(r.day1Date).toBe("2026-06-05");
    expect(r.day2Date).toBe("2026-06-06");
    expect(r.day3Date).toBe("2026-06-07");
  });

  it("returns three empty day arrays (and still-correct dates) for empty input", () => {
    const r = distributeCatchUpFlush([], new Date("2026-05-01T00:00:00Z"));
    expect(r.day1).toEqual([]);
    expect(r.day2).toEqual([]);
    expect(r.day3).toEqual([]);
    expect(r.day1Date).toBe("2026-05-02");
  });

  it("drops none and duplicates none across a larger input", () => {
    const ids = Array.from({ length: 10 }, (_, i) => `ayah-${i}`);
    const r = distributeCatchUpFlush(ids, new Date("2026-04-01T00:00:00Z"));
    const all = [...r.day1, ...r.day2, ...r.day3].sort();
    expect(all).toEqual([...ids].sort());
    expect(r.day1.length + r.day2.length + r.day3.length).toBe(10);
  });

  it("does not mutate the input array", () => {
    // Deliberately NOT already sorted -- an input like ["a","b","c"] would
    // make an in-place .sort() mutation invisible to this test.
    const ids = ["c", "a", "b"];
    const copy = [...ids];
    distributeCatchUpFlush(ids, new Date("2026-03-10T00:00:00Z"));
    expect(ids).toEqual(copy);
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
