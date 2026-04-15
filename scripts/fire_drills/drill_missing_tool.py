"""Drill 5 — test tool absent → pause, not crash.

test_gate.run_tests chooses a command based on repo shape (npm test,
python -m pytest, etc.). When the tool is absent from PATH, the drill
must confirm the failure is surfaced as a structured failure result,
NOT a crash that takes down the orchestrator poll loop.

We run test_gate.run_tests against a repo with package.json present but
with PATH stripped to just /dev/null — npm cannot be found. Expected:
test_gate returns {passed: False, ...}. It must NOT raise to the caller.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from scripts.fire_drills.base import Drill


class MissingToolDrill(Drill):
    NAME = "fire_drill_missing_tool"
    GATE_REF = "fire_drill_missing_tool"

    async def setup(self) -> dict[str, Any]:
        repo_dir = tempfile.mkdtemp(prefix="ihsan_drill_missing_tool_")
        repo_path = Path(repo_dir)
        # package.json with a test script — triggers the npm branch in test_gate.
        (repo_path / "package.json").write_text(
            json.dumps({"name": "drill", "scripts": {"test": "vitest"}})
        )
        return {"repo_path": repo_dir}

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        import test_gate

        # Mock supabase so test_gate's log_to_supabase doesn't hit a real DB.
        sb = AsyncMock()
        sb.table.return_value = sb
        sb.insert.return_value = sb
        sb.execute = AsyncMock(return_value=AsyncMock(data=[]))

        # Patch PATH so npm cannot be located. test_gate builds env from
        # SAFE_ENV_KEYS so we monkeypatch via the imported constant.
        import os
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = "/nonexistent-drill-path"

        raised_exception = None
        result = None
        try:
            result = await test_gate.run_tests(
                state["repo_path"], "drill-fake", 99999, sb
            )
        except Exception as e:
            raised_exception = f"{type(e).__name__}: {e}"
        finally:
            os.environ["PATH"] = original_path

        return {
            "test_gate_result": result,
            "raised_exception": raised_exception,
        }

    async def assert_outcome(self, state: dict[str, Any], run_output: dict[str, Any]) -> None:
        assert run_output["raised_exception"] is None, (
            f"test_gate crashed instead of returning a failure result: "
            f"{run_output['raised_exception']}"
        )
        result = run_output["test_gate_result"]
        assert isinstance(result, dict), (
            f"test_gate returned non-dict: {result!r}"
        )
        assert result.get("passed") is False, (
            f"test_gate reported pass when the tool was absent: {result}"
        )
        output = result.get("output", "")
        assert isinstance(output, str), "output must be a string"

    async def cleanup(self, state: dict[str, Any]) -> None:
        repo_path = state.get("repo_path")
        if repo_path and Path(repo_path).exists():
            shutil.rmtree(repo_path, ignore_errors=True)


if __name__ == "__main__":
    import sys
    from scripts.fire_drills.base import run_drill_sync
    dry = "--dry-run" in sys.argv
    result = run_drill_sync(MissingToolDrill(), dry_run=dry)
    print(result)
    sys.exit(0 if result.passed else 1)
