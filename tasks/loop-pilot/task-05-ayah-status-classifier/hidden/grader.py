#!/usr/bin/env python3
"""grader.py for task-05-ayah-status-classifier (op#35690,
docs/loop-pilot-design-v1.md section 6a). Ground truth for
classifyAyahStatus() -- independent of whatever the spec-writer's own
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

TASK_ID = "task-05-ayah-status-classifier"
IMPL_REL = "web/src/modules/scheduler/ayahStatusClassifier.ts"
ORACLE_REL = "web/src/modules/scheduler/__tests__/_grader_oracle_task-05-ayah-status-classifier.test.ts"

ORACLE_TS = r"""
// Transient oracle test for task-05-ayah-status-classifier, written by
// grader.py. Not part of the repo's real test suite -- deleted after this
// one run.
import { describe, it, expect } from "vitest";
import { classifyAyahStatus } from "../ayahStatusClassifier";

function mem(retrievability: number, isLeech = false) {
  return { retrievability, isLeech };
}
function taj(lastGrade: 1 | 2 | 3 | 4) {
  return { lastGrade };
}

describe("oracle: classifyAyahStatus", () => {
  it("fluent-and-correct: high retrievability, not leech, good tajweed grade", () => {
    const r = classifyAyahStatus(mem(0.95), taj(4));
    expect(r.hifzFluent).toBe(true);
    expect(r.tajweedWeak).toBe(false);
    expect(r.combinedLabel).toBe("fluent-and-correct");
  });

  it("fluent-tajweed-weak: high retrievability, not leech, weak tajweed grade", () => {
    const r = classifyAyahStatus(mem(0.95), taj(1));
    expect(r.hifzFluent).toBe(true);
    expect(r.tajweedWeak).toBe(true);
    expect(r.combinedLabel).toBe("fluent-tajweed-weak");
  });

  it("weak-hifz-tajweed-ok: low retrievability, good tajweed grade", () => {
    const r = classifyAyahStatus(mem(0.3), taj(3));
    expect(r.hifzFluent).toBe(false);
    expect(r.tajweedWeak).toBe(false);
    expect(r.combinedLabel).toBe("weak-hifz-tajweed-ok");
  });

  it("weak-both: low retrievability and weak tajweed grade", () => {
    const r = classifyAyahStatus(mem(0.3), taj(2));
    expect(r.hifzFluent).toBe(false);
    expect(r.tajweedWeak).toBe(true);
    expect(r.combinedLabel).toBe("weak-both");
  });

  it("retrievability exactly 0.9 counts as fluent (inclusive boundary)", () => {
    const r = classifyAyahStatus(mem(0.9), taj(4));
    expect(r.hifzFluent).toBe(true);
  });

  it("retrievability just under 0.9 does not count as fluent", () => {
    const r = classifyAyahStatus(mem(0.8999), taj(4));
    expect(r.hifzFluent).toBe(false);
  });

  it("a leech overrides high retrievability -- not fluent", () => {
    const r = classifyAyahStatus(mem(0.99, true), taj(4));
    expect(r.hifzFluent).toBe(false);
  });

  it("lastGrade 2 is weak, lastGrade 3 is not (exact boundary)", () => {
    expect(classifyAyahStatus(mem(0.95), taj(2)).tajweedWeak).toBe(true);
    expect(classifyAyahStatus(mem(0.95), taj(3)).tajweedWeak).toBe(false);
  });

  it("H-8 no-cross-contamination: hifzFluent unaffected by a low tajweed grade", () => {
    const high = classifyAyahStatus(mem(0.95), taj(1)).hifzFluent;
    const low = classifyAyahStatus(mem(0.95), taj(4)).hifzFluent;
    expect(high).toBe(true);
    expect(low).toBe(true);
    expect(high).toBe(low);
  });

  it("H-8 no-cross-contamination: tajweedWeak unaffected by a low retrievability", () => {
    const highR = classifyAyahStatus(mem(0.99), taj(3)).tajweedWeak;
    const lowR = classifyAyahStatus(mem(0.1), taj(3)).tajweedWeak;
    expect(highR).toBe(false);
    expect(lowR).toBe(false);
    expect(highR).toBe(lowR);
  });

  it("H-8 no-cross-contamination: tajweedWeak unaffected by isLeech", () => {
    const notLeech = classifyAyahStatus(mem(0.95, false), taj(4)).tajweedWeak;
    const isLeech = classifyAyahStatus(mem(0.95, true), taj(4)).tajweedWeak;
    expect(notLeech).toBe(false);
    expect(isLeech).toBe(false);
    expect(notLeech).toBe(isLeech);
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
