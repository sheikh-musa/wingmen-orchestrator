#!/usr/bin/env python3
"""Run all Phase 0 fire drills end-to-end and record results.

Per ARCH-016 Phase 0:
  - Execute each of the 5 drills against the live orchestrator stack
  - Record outcome (pass/fail + duration + error if any) to
    bug_pipeline_readiness.notes
  - If all 5 pass: flip each row to status='green' and reset the
    14-day clean clock (days_clean=0, last_breach_at=NULL)
  - If any fail: leave that row at 'pending', append the error to
    notes for triage

Usage:
  python -m scripts.run_phase0_drills [--dry-run]

Dry-run mode only exercises setup/cleanup of each drill — used to
verify the harness still scaffolds cleanly without injecting real
failure conditions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a script from anywhere
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from scripts.fire_drills.base import Drill, DrillResult
from scripts.fire_drills.drill_broken_migration import BrokenMigrationDrill
from scripts.fire_drills.drill_cli_timeout import CliTimeoutDrill
from scripts.fire_drills.drill_concurrent_conflict import ConcurrentConflictDrill
from scripts.fire_drills.drill_missing_tool import MissingToolDrill
from scripts.fire_drills.drill_sigkill import SigkillDrill

load_dotenv(Path(__file__).parent.parent / ".env")


DRILLS: list[type[Drill]] = [
    BrokenMigrationDrill,
    SigkillDrill,
    ConcurrentConflictDrill,
    CliTimeoutDrill,
    MissingToolDrill,
]


async def execute_one(drill: Drill, dry_run: bool) -> DrillResult:
    print(f"\n▶  {drill.NAME} (gate_ref={drill.GATE_REF})")
    result = await drill.execute(dry_run=dry_run)
    status = "✓ PASS" if result.passed else "✗ FAIL"
    print(f"   {status}  duration={result.duration_seconds}s")
    if result.error:
        print(f"   error: {result.error}")
    return result


async def record_results(results: list[DrillResult], dry_run: bool) -> None:
    """Write outcomes to bug_pipeline_readiness via supabase REST.

    Updates `notes` for every drill (pass or fail), and flips
    status='green' only on pass. Keeps days_clean=0 — the clean clock
    starts ticking from now; the operator decides when to declare a
    breach if subsequent regressions occur.
    """
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    client = create_client(url, key)

    ran_at = datetime.now(timezone.utc).isoformat()
    for r in results:
        gate_ref = next(
            (d.GATE_REF for d in DRILLS if d.NAME == r.name and d.GATE_REF), None
        )
        if not gate_ref:
            print(f"!  no gate_ref for {r.name}, skipping record")
            continue

        outcome = "PASS" if r.passed else "FAIL"
        new_note = (
            f"[live drill {ran_at}] {outcome} duration={r.duration_seconds}s"
            + (f" error={r.error}" if r.error else "")
            + (" (dry-run)" if dry_run else "")
        )

        update = {"notes": new_note, "updated_at": ran_at}
        if r.passed and not dry_run:
            update["status"] = "green"
            update["last_breach_at"] = None
            update["last_breach_reason"] = None

        client.table("bug_pipeline_readiness").update(update).eq(
            "gate_ref", gate_ref
        ).execute()
        print(f"   recorded → bug_pipeline_readiness.{gate_ref}: {outcome}")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Run setup/cleanup only, no real injection"
    )
    parser.add_argument(
        "--no-record", action="store_true", help="Skip writing results to Supabase"
    )
    args = parser.parse_args(argv)

    print("Phase 0 fire drill runner")
    print(f"  dry_run={args.dry_run}  no_record={args.no_record}")

    results: list[DrillResult] = []
    for cls in DRILLS:
        results.append(await execute_one(cls(), dry_run=args.dry_run))

    print("\n" + "─" * 60)
    print("Summary:")
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    for r in results:
        marker = "✓" if r.passed else "✗"
        print(f"  {marker} {r.name:<30} {r.duration_seconds:>6}s")
    print(f"  → {passed}/{len(results)} passed")

    if not args.no_record:
        print("\nRecording outcomes…")
        await record_results(results, dry_run=args.dry_run)

    print("\n" + json.dumps(
        {
            "passed": passed,
            "failed": failed,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": args.dry_run,
        },
        indent=2,
    ))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
