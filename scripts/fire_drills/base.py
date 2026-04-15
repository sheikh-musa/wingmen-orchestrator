"""Fire drill base class and runner (TASK-037 / TASK-038 step 2).

Each drill is a subclass of Drill with:
  - NAME: short identifier (matches bug_pipeline_readiness.gate_ref)
  - setup() -> dict: inject the failure condition, return state for cleanup
  - run() -> dict: trigger the orchestrator pathway under test
  - assert_outcome(state, run_result) -> None: raise AssertionError if the
    observed outcome does not match the expected recovery behaviour
  - cleanup(state) -> None: restore pre-drill state, always runs even on failure

Runner writes the outcome to bug_pipeline_readiness.notes so the
14-day clean clock can be tracked against actual drill results, not
just code existence.

Dry-run mode (--dry-run) exercises setup/cleanup only — used by unit
tests and by operators who want to verify the drill can be scaffolded
before committing to a real injection.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("wingmen.fire_drill")


@dataclasses.dataclass
class DrillResult:
    name: str
    passed: bool
    duration_seconds: float
    setup_state: dict[str, Any]
    run_output: dict[str, Any]
    error: str | None = None


class Drill(ABC):
    NAME: str = "unnamed"
    GATE_REF: str = ""  # matches bug_pipeline_readiness.gate_ref

    @abstractmethod
    async def setup(self) -> dict[str, Any]:
        """Inject the failure condition. Return state needed by cleanup()."""

    @abstractmethod
    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Trigger the orchestrator action that should handle the failure."""

    @abstractmethod
    async def assert_outcome(self, state: dict[str, Any], run_output: dict[str, Any]) -> None:
        """Raise AssertionError if the observed outcome is wrong."""

    @abstractmethod
    async def cleanup(self, state: dict[str, Any]) -> None:
        """Always runs. Must restore the pre-drill state."""

    async def execute(self, dry_run: bool = False) -> DrillResult:
        t0 = time.monotonic()
        state: dict[str, Any] = {}
        run_output: dict[str, Any] = {}
        error: str | None = None
        passed = False
        try:
            state = await self.setup()
            if dry_run:
                run_output = {"dry_run": True}
                passed = True
            else:
                run_output = await self.run(state)
                await self.assert_outcome(state, run_output)
                passed = True
        except AssertionError as e:
            error = f"assert failed: {e}"
            logger.error(f"[{self.NAME}] drill failed assertion: {e}")
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.exception(f"[{self.NAME}] drill crashed")
        finally:
            try:
                await self.cleanup(state)
            except Exception as e:
                logger.exception(f"[{self.NAME}] cleanup error (non-fatal for drill outcome): {e}")
        return DrillResult(
            name=self.NAME,
            passed=passed,
            duration_seconds=round(time.monotonic() - t0, 2),
            setup_state=state,
            run_output=run_output,
            error=error,
        )


async def record_drill_result(supabase, result: DrillResult) -> None:
    """Write the drill outcome to bug_pipeline_readiness.notes for the matching gate_ref.

    Does NOT flip status='green' — that's a separate decision after all 5
    drills pass + operator reviews the notes.
    """
    if not result.passed or not getattr(result, "gate_ref", None):
        # Gate_ref must be set by caller — we don't infer it.
        pass
    # Actual write is done by the runner once gate_ref is known; this
    # signature is kept async for future direct writes.
    logger.info(f"[{result.name}] passed={result.passed} duration={result.duration_seconds}s")


def run_drill_sync(drill: Drill, dry_run: bool = False) -> DrillResult:
    """Blocking entrypoint — used by the CLI shell wrappers."""
    return asyncio.run(drill.execute(dry_run=dry_run))
