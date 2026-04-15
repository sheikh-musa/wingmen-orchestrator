"""Drill 3 — concurrent conflicting bugs serialize cleanly, no silent corruption.

Two async jobs race to claim the same `jobs` row via the pick_next_jobs
path (or any writer that uses optimistic status transitions). Exactly one
should succeed in claiming to 'running'; the other should see the row
already taken and skip it. Neither should silently overwrite the other.

We simulate this with an in-memory jobs structure where the UPDATE uses
a WHERE status='queued' pre-condition — the real DB semantics. Two tasks
race; exactly one wins the claim.
"""

from __future__ import annotations

import asyncio
from typing import Any

from scripts.fire_drills.base import Drill


class _MockJobsDb:
    """Tiny in-memory DB with optimistic locking on status transitions."""

    def __init__(self):
        self._jobs = [{"id": 1, "status": "queued", "claimed_by": None}]
        self._lock = asyncio.Lock()

    async def try_claim(self, worker_id: str) -> bool:
        # Real Supabase would use UPDATE ... WHERE status='queued' RETURNING *
        # to atomically claim. We simulate that atomicity.
        async with self._lock:
            for job in self._jobs:
                if job["status"] == "queued":
                    job["status"] = "running"
                    job["claimed_by"] = worker_id
                    return True
            return False

    def state(self):
        return [dict(j) for j in self._jobs]


class ConcurrentConflictDrill(Drill):
    NAME = "fire_drill_conflict"
    GATE_REF = "fire_drill_conflict"

    async def setup(self) -> dict[str, Any]:
        return {"db": _MockJobsDb()}

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        db: _MockJobsDb = state["db"]
        results = await asyncio.gather(
            db.try_claim("worker-A"),
            db.try_claim("worker-B"),
        )
        return {
            "claim_a": results[0],
            "claim_b": results[1],
            "final_state": db.state(),
        }

    async def assert_outcome(self, state: dict[str, Any], run_output: dict[str, Any]) -> None:
        claim_a = run_output["claim_a"]
        claim_b = run_output["claim_b"]
        assert claim_a != claim_b, (
            f"race condition: both workers claimed (a={claim_a}, b={claim_b}) — "
            f"atomicity broken"
        )
        assert claim_a or claim_b, (
            f"neither worker claimed (a={claim_a}, b={claim_b}) — livelock"
        )
        final = run_output["final_state"]
        assert len(final) == 1 and final[0]["status"] == "running", (
            f"final job state wrong: {final}"
        )
        assert final[0]["claimed_by"] in ("worker-A", "worker-B"), (
            f"claimed_by not set to a worker: {final[0]}"
        )

    async def cleanup(self, state: dict[str, Any]) -> None:
        # In-memory, nothing to clean.
        pass


if __name__ == "__main__":
    import sys
    from scripts.fire_drills.base import run_drill_sync
    dry = "--dry-run" in sys.argv
    result = run_drill_sync(ConcurrentConflictDrill(), dry_run=dry)
    print(result)
    sys.exit(0 if result.passed else 1)
