"""Drill 2 — SIGKILL mid-CLI leaves no zombies after orchestrator restart.

Real semantics: a long-running CLI subprocess is SIGKILL'd. We simulate
the job-row that would correspond to it by creating a fake 'running' row
in a test jobs-table surrogate (in-memory), then invoking the REAL
cleanup_zombie_jobs logic, then asserting the row is marked failed + has
fail_count bumped. The CLI subprocess kill itself is exercised too — we
spawn a 60s sleep, kill it, assert the kill returns.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from scripts.fire_drills.base import Drill


class SigkillDrill(Drill):
    NAME = "fire_drill_sigkill"
    GATE_REF = "fire_drill_sigkill"

    async def setup(self) -> dict[str, Any]:
        # 1. Spawn a subprocess we can actually kill (proves SIGKILL works in this env).
        proc = await asyncio.create_subprocess_exec(
            "sleep", "60",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # 2. Build a mock Supabase with a pretend 'running' job — this is what
        #    cleanup_zombie_jobs would see after the orchestrator restarted.
        zombie_jobs = [
            {"id": 9999, "repo_name": "drill-fake-repo", "fail_count": 1},
        ]
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(
            side_effect=[MagicMock(data=zombie_jobs), MagicMock(data=[])]
        )
        return {"proc": proc, "sb": sb, "zombie_jobs": zombie_jobs}

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        from wingmen_orch import cleanup_zombie_jobs

        proc = state["proc"]
        # 1. Actually SIGKILL the subprocess.
        os.kill(proc.pid, signal.SIGKILL)
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass

        # 2. Invoke the real cleanup function against the mocked DB.
        count = await cleanup_zombie_jobs(state["sb"])

        # 3. Capture the update call so assert_outcome can inspect it.
        update_call = state["sb"].update.call_args[0][0] if state["sb"].update.called else None

        return {
            "proc_exit_code": proc.returncode,
            "cleanup_count": count,
            "update_arg": update_call,
        }

    async def assert_outcome(self, state: dict[str, Any], run_output: dict[str, Any]) -> None:
        # Subprocess must have died from the signal.
        assert run_output["proc_exit_code"] is not None, (
            "subprocess did not exit after SIGKILL — kill semantics broken in this env"
        )
        # Cleanup must have swept the one zombie.
        assert run_output["cleanup_count"] == 1, (
            f"cleanup_zombie_jobs returned {run_output['cleanup_count']} — expected 1"
        )
        upd = run_output["update_arg"] or {}
        assert upd.get("status") == "failed", (
            f"zombie status should be 'failed', got {upd.get('status')}"
        )
        assert upd.get("fail_count") == 2, (
            f"fail_count should have bumped 1 -> 2, got {upd.get('fail_count')}"
        )

    async def cleanup(self, state: dict[str, Any]) -> None:
        proc = state.get("proc")
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=2)
            except Exception:
                pass


if __name__ == "__main__":
    import sys
    from scripts.fire_drills.base import run_drill_sync
    dry = "--dry-run" in sys.argv
    result = run_drill_sync(SigkillDrill(), dry_run=dry)
    print(result)
    sys.exit(0 if result.passed else 1)
