"""Drill 4 — Claude CLI timeout with partial work → WIP branch + pause, not lost.

Runs a subprocess that writes a file then hangs past its timeout. The real
ralph_runner treats this as the crash path: partial work must not be
silently discarded. We exercise the asyncio.wait_for-timeout semantics that
ralph_runner depends on, confirming:
  - timeout fires
  - partial work (the file) survives
  - subprocess is killed (no leak)

The actual ralph_runner WIP-branch push is a separate integration step
that only makes sense against a real repo — this drill proves the
timeout+cleanup foundation on which that is built.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scripts.fire_drills.base import Drill


class CliTimeoutDrill(Drill):
    NAME = "fire_drill_cli_timeout"
    GATE_REF = "fire_drill_cli_timeout"

    async def setup(self) -> dict[str, Any]:
        work_dir = tempfile.mkdtemp(prefix="ihsan_drill_timeout_")
        work_path = Path(work_dir)
        marker = work_path / "partial_work.txt"
        return {"work_dir": work_dir, "marker_path": str(marker)}

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        marker_path = state["marker_path"]

        # Subprocess: write a file, then sleep 10s. We'll time out at 1s.
        proc = await asyncio.create_subprocess_shell(
            f"echo 'partial work committed' > {marker_path} && sleep 10",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timed_out = False
        try:
            await asyncio.wait_for(proc.communicate(), timeout=1)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

        return {
            "timed_out": timed_out,
            "proc_returncode": proc.returncode,
            "partial_work_survived": Path(marker_path).exists(),
            "partial_work_content": (
                Path(marker_path).read_text().strip() if Path(marker_path).exists() else None
            ),
        }

    async def assert_outcome(self, state: dict[str, Any], run_output: dict[str, Any]) -> None:
        assert run_output["timed_out"] is True, (
            "wait_for did not time out — drill cannot simulate CLI hangs"
        )
        assert run_output["partial_work_survived"] is True, (
            "partial work file was lost after timeout+kill — real ralph_runner "
            "would lose user commits here"
        )
        assert run_output["partial_work_content"] == "partial work committed", (
            f"partial work corrupted: {run_output['partial_work_content']!r}"
        )
        # Process must actually have been killed, not leaked.
        assert run_output["proc_returncode"] is not None, (
            "subprocess still alive after kill+wait — leak risk"
        )

    async def cleanup(self, state: dict[str, Any]) -> None:
        work_dir = state.get("work_dir")
        if work_dir and Path(work_dir).exists():
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    import sys
    from scripts.fire_drills.base import run_drill_sync
    dry = "--dry-run" in sys.argv
    result = run_drill_sync(CliTimeoutDrill(), dry_run=dry)
    print(result)
    sys.exit(0 if result.passed else 1)
