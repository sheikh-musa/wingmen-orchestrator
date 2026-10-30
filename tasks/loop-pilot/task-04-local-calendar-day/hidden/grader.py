#!/usr/bin/env python3
"""grader.py for task-04-local-calendar-day (op#35690,
docs/loop-pilot-design-v1.md section 6a). Ground truth for
localCalendarDayKey()/isSameLocalDay() -- independent of whatever the
spec-writer's own acceptance tests happened to cover.

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

TASK_ID = "task-04-local-calendar-day"
IMPL_REL = "web/src/modules/scheduler/localCalendarDay.ts"
ORACLE_REL = "web/src/modules/scheduler/__tests__/_grader_oracle_task-04-local-calendar-day.test.ts"

ORACLE_TS = r"""
// Transient oracle test for task-04-local-calendar-day, written by
// grader.py. Not part of the repo's real test suite -- deleted after this
// one run. Fixtures double-checked against real IANA tzdata (node -e
// with Intl.DateTimeFormat) before being committed to this file.
import { describe, it, expect } from "vitest";
import { localCalendarDayKey, isSameLocalDay } from "../localCalendarDay";

describe("oracle: localCalendarDayKey / isSameLocalDay", () => {
  it("a UTC timestamp already 'tomorrow' in a positive-offset zone (Asia/Tokyo, +9, no DST)", () => {
    const d = new Date("2026-06-10T22:00:00Z");
    expect(localCalendarDayKey(d, "Asia/Tokyo")).toBe("2026-06-11");
  });

  it("a UTC timestamp already 'yesterday' in a negative-offset zone (America/New_York, EDT -4 in July)", () => {
    const d = new Date("2026-07-15T02:00:00Z");
    expect(localCalendarDayKey(d, "America/New_York")).toBe("2026-07-14");
  });

  it("resolves the real EDT offset in effect, not a hardcoded -4 assumption year-round (EST -5 in early March, before 2026's spring-forward)", () => {
    // 2026-03-08T04:30:00Z is still EST (spring-forward happens at
    // 2026-03-08T07:00:00Z) -- correct local calendar day is 2026-03-07.
    // A hardcoded-EDT(-4)-always implementation would wrongly get 2026-03-08.
    const d = new Date("2026-03-08T04:30:00Z");
    expect(localCalendarDayKey(d, "America/New_York")).toBe("2026-03-07");
  });

  it("resolves the real EDT offset in effect, not a hardcoded -5 assumption year-round (EDT -4 in July)", () => {
    // Correct local calendar day is 2026-07-15 (EDT, -4). A
    // hardcoded-EST(-5)-always implementation would wrongly get 2026-07-14.
    const d = new Date("2026-07-15T04:30:00Z");
    expect(localCalendarDayKey(d, "America/New_York")).toBe("2026-07-15");
  });

  it("outputs exactly YYYY-MM-DD, zero-padded, for a single-digit month and day", () => {
    const d = new Date("2026-01-05T12:00:00Z");
    const key = localCalendarDayKey(d, "UTC");
    expect(key).toBe("2026-01-05");
    expect(key).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("isSameLocalDay: true for instants on DIFFERENT UTC days but the SAME local day (Asia/Tokyo)", () => {
    const a = new Date("2026-06-10T23:30:00Z"); // UTC day 06-10
    const b = new Date("2026-06-11T00:30:00Z"); // UTC day 06-11
    expect(isSameLocalDay(a, b, "Asia/Tokyo")).toBe(true);
  });

  it("isSameLocalDay: false for instants on the SAME UTC day but DIFFERENT local days (Pacific/Honolulu, -10, no DST) -- also catches isSameLocalDay silently defaulting to UTC internally", () => {
    const a = new Date("2026-01-01T01:00:00Z");
    const b = new Date("2026-01-01T23:00:00Z");
    expect(isSameLocalDay(a, b, "Pacific/Honolulu")).toBe(false);
  });

  it("isSameLocalDay: true for a plain same-UTC-day, same-local-day pair in UTC itself", () => {
    const a = new Date("2026-05-01T01:00:00Z");
    const b = new Date("2026-05-01T23:00:00Z");
    expect(isSameLocalDay(a, b, "UTC")).toBe(true);
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
