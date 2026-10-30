#!/usr/bin/env python3
"""grader.py for task-03-oracle-audit-plan (op#35690,
docs/loop-pilot-design-v1.md section 6a). Ground truth for
computeOracleAuditPlan() -- independent of whatever the spec-writer's own
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

TASK_ID = "task-03-oracle-audit-plan"
IMPL_REL = "web/src/modules/scheduler/oracleAuditPlan.ts"
ORACLE_REL = "web/src/modules/scheduler/__tests__/_grader_oracle_task-03-oracle-audit-plan.test.ts"

ORACLE_TS = r"""
// Transient oracle test for task-03-oracle-audit-plan, written by
// grader.py. Not part of the repo's real test suite -- deleted after this
// one run.
import { describe, it, expect } from "vitest";
import { computeOracleAuditPlan } from "../oracleAuditPlan";

function mk(ayahId: string, retrievability: number, intervalDays: number) {
  return { ayahId, retrievability, intervalDays };
}

describe("oracle: computeOracleAuditPlan", () => {
  it("eligible by retrievability alone (R >= 0.95, short interval)", () => {
    const r = computeOracleAuditPlan([mk("a", 0.95, 10)]);
    expect(r.eligibleAyahIds).toEqual(["a"]);
  });

  it("eligible by interval alone (> 60 days, low R)", () => {
    const r = computeOracleAuditPlan([mk("a", 0.5, 61)]);
    expect(r.eligibleAyahIds).toEqual(["a"]);
  });

  it("interval exactly 60 is NOT eligible via the interval condition", () => {
    const r = computeOracleAuditPlan([mk("a", 0.5, 60)]);
    expect(r.eligibleAyahIds).toEqual([]);
  });

  it("eligible by both conditions still appears once", () => {
    const r = computeOracleAuditPlan([mk("a", 0.99, 90)]);
    expect(r.eligibleAyahIds).toEqual(["a"]);
  });

  it("not eligible by either condition is excluded", () => {
    const r = computeOracleAuditPlan([mk("a", 0.5, 10)]);
    expect(r.eligibleAyahIds).toEqual([]);
  });

  it("eligible pool of 49 -> sampleSize 5", () => {
    const states = Array.from({ length: 49 }, (_, i) => mk(`a${i}`, 0.99, 10));
    const r = computeOracleAuditPlan(states);
    expect(r.eligibleAyahIds.length).toBe(49);
    expect(r.sampleSize).toBe(5);
  });

  it("eligible pool of exactly 50 -> sampleSize 10 (inclusive boundary)", () => {
    const states = Array.from({ length: 50 }, (_, i) => mk(`a${i}`, 0.99, 10));
    const r = computeOracleAuditPlan(states);
    expect(r.eligibleAyahIds.length).toBe(50);
    expect(r.sampleSize).toBe(10);
  });

  it("threshold is based on the ELIGIBLE pool size, not the raw input size", () => {
    // 52 total entries (>= 50, so a total-size-based implementation would
    // wrongly pick base 10), but only 7 are eligible (< 50, correct base
    // is 5). Eligible pool (7) is bigger than the correct base (5) but
    // smaller than the wrong base (10), so capping can't mask the bug:
    // correct answer is min(5, 7) = 5; a total-size-based bug gives
    // min(10, 7) = 7.
    const ineligible = Array.from({ length: 45 }, (_, i) => mk(`x${i}`, 0.5, 10));
    const eligible = Array.from({ length: 7 }, (_, i) => mk(`e${i}`, 0.99, 10));
    const r = computeOracleAuditPlan([...ineligible, ...eligible]);
    expect(r.eligibleAyahIds.length).toBe(7);
    expect(r.sampleSize).toBe(5);
  });

  it("caps sampleSize to the eligible pool size when it's smaller than the base", () => {
    const states = [mk("a", 0.99, 10), mk("b", 0.99, 10)];
    const r = computeOracleAuditPlan(states);
    expect(r.sampleSize).toBe(2);
  });

  it("preserves duplicate ayahIds and input order in eligibleAyahIds", () => {
    const states = [mk("dup", 0.99, 10), mk("other", 0.5, 10), mk("dup", 0.99, 10)];
    const r = computeOracleAuditPlan(states);
    expect(r.eligibleAyahIds).toEqual(["dup", "dup"]);
  });

  it("does not mutate the input array", () => {
    const states = [mk("c", 0.5, 10), mk("a", 0.99, 90), mk("b", 0.5, 5)];
    const copy = states.map((s) => ({ ...s }));
    computeOracleAuditPlan(states);
    expect(states).toEqual(copy);
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
