#!/usr/bin/env python3
"""grader.py for task-06-fsrs-export-payload (op#35690,
docs/loop-pilot-design-v1.md section 6a). Ground truth for
createFsrsExportPayload() -- independent of whatever the spec-writer's own
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

TASK_ID = "task-06-fsrs-export-payload"
IMPL_REL = "web/src/modules/backup/fsrsExportPayload.ts"
ORACLE_REL = "web/src/modules/backup/__tests__/_grader_oracle_task-06-fsrs-export-payload.test.ts"

ORACLE_TS = r"""
// Transient oracle test for task-06-fsrs-export-payload, written by
// grader.py. Not part of the repo's real test suite -- deleted after this
// one run.
import { describe, it, expect, vi, afterEach } from "vitest";
import { createFsrsExportPayload } from "../fsrsExportPayload";

afterEach(() => {
  vi.useRealTimers();
});

describe("oracle: createFsrsExportPayload", () => {
  it("schemaVersion is exactly the string '1'", () => {
    const r = createFsrsExportPayload([], [], [], new Date("2026-05-01T00:00:00Z"));
    expect(r.schemaVersion).toBe("1");
    expect(typeof r.schemaVersion).toBe("string");
  });

  it("exportedAt is the explicit `now` param, never the real/faked system clock", () => {
    // System clock is faked to a DIFFERENT moment than `now` -- if the
    // implementation calls Date.now()/new Date() internally instead of
    // using the `now` parameter, exportedAt will reflect the fake system
    // time instead of the explicit one.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2099-01-01T00:00:00Z"));
    const explicitNow = new Date("2026-05-01T12:34:56Z");
    const r = createFsrsExportPayload([], [], [], explicitNow);
    expect(r.exportedAt).toBe(explicitNow.toISOString());
    expect(r.exportedAt).not.toBe(new Date("2099-01-01T00:00:00Z").toISOString());
  });

  it("rowCounts matches each input array's raw length", () => {
    const r = createFsrsExportPayload(
      [{ id: 1 }, { id: 2 }],
      [{ id: "a" }],
      [],
      new Date("2026-05-01T00:00:00Z"),
    );
    expect(r.rowCounts).toEqual({ state: 2, events: 1, auditLog: 0 });
  });

  it("preserves all rows, in order, across all three arrays", () => {
    const state = [{ id: 1 }, { id: 2 }, { id: 3 }];
    const events = [{ id: "e1" }, { id: "e2" }];
    const auditLog = [{ id: "a1" }];
    const r = createFsrsExportPayload(state, events, auditLog, new Date("2026-05-01T00:00:00Z"));
    expect(r.state).toEqual(state);
    expect(r.events).toEqual(events);
    expect(r.auditLog).toEqual(auditLog);
  });

  it("a duplicate row object appearing twice in the input appears twice in the output (not deduped)", () => {
    const row = { id: "dup", value: 42 };
    const state = [row, row];
    const r = createFsrsExportPayload(state, [], [], new Date("2026-05-01T00:00:00Z"));
    expect(r.state.length).toBe(2);
    expect(r.state).toEqual([row, row]);
  });

  it("mutating the ORIGINAL array after the call does not change the already-returned payload", () => {
    const state = [{ id: 1, status: "original" }];
    const r = createFsrsExportPayload(state, [], [], new Date("2026-05-01T00:00:00Z"));
    state[0].status = "mutated-after-export";
    state.push({ id: 2, status: "added-after-export" });
    expect(r.state).toEqual([{ id: 1, status: "original" }]);
  });

  it("does not mutate the original input arrays while building the payload", () => {
    const state = [{ id: 3 }, { id: 1 }, { id: 2 }];
    const copy = state.map((s) => ({ ...s }));
    createFsrsExportPayload(state, [], [], new Date("2026-05-01T00:00:00Z"));
    expect(state).toEqual(copy);
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
