"""Tests for ralph_runner module (mocked subprocess)."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import ralph_runner


@pytest.fixture
def mock_supabase():
    mock = MagicMock()
    mock.table.return_value.insert.return_value.execute = AsyncMock(
        return_value=MagicMock(data=[])
    )
    return mock


def _make_process(stdout: str, stderr: str = "", returncode: int = 0):
    """Create a mock async subprocess."""
    process = AsyncMock()
    process.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    process.returncode = returncode
    return process


@pytest.mark.asyncio
async def test_run_claude_success(mock_supabase, tmp_path):
    """Successful run with completion promise in output."""
    stdout = "Building...\nDone!\nJOB_1_DONE\nAll tests pass."
    process = _make_process(stdout)

    with patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Fix the bug",
            job_id=1,
            repo_name="test-repo",
            supabase=mock_supabase,
        )

    assert result["success"] is True
    assert "JOB_1_DONE" in result["summary"]


@pytest.mark.asyncio
async def test_run_claude_failure_no_promise(mock_supabase, tmp_path):
    """Failed run — no completion promise in output."""
    stdout = "Building...\nError: something broke"
    process = _make_process(stdout, returncode=1)

    with patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Fix the bug",
            job_id=2,
            repo_name="test-repo",
            supabase=mock_supabase,
        )

    assert result["success"] is False


@pytest.mark.asyncio
async def test_run_claude_timeout(mock_supabase, tmp_path):
    """Timeout should return failure."""
    process = AsyncMock()
    process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

    with patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Slow task",
            job_id=3,
            repo_name="test-repo",
            supabase=mock_supabase,
        )

    assert result["success"] is False
    assert "timed out" in result["summary"]


@pytest.mark.asyncio
async def test_run_claude_logs_to_supabase(mock_supabase, tmp_path):
    """Verify build log entries are written to Supabase."""
    stdout = "output\nJOB_4_DONE"
    process = _make_process(stdout)

    with patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process):
        await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Task",
            job_id=4,
            repo_name="test-repo",
            supabase=mock_supabase,
        )

    # Should have logged: claude_start, claude_stdout, claude_done (at minimum)
    insert_calls = mock_supabase.table.return_value.insert.call_args_list
    assert len(insert_calls) >= 2  # start + done at minimum


@pytest.mark.asyncio
async def test_run_claude_exception(mock_supabase, tmp_path):
    """Unexpected exception should be caught and returned."""
    with patch(
        "ralph_runner.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("claude not found"),
    ):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Task",
            job_id=5,
            repo_name="test-repo",
            supabase=mock_supabase,
        )

    assert result["success"] is False
    assert "claude not found" in result["summary"]


# ── ARCH-021 Ghost-success regression tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_gate1_ghost_success_prevented(mock_supabase, tmp_path):
    """ARCH-021 Gate 1: exit-0 + output but no commit → success=False."""
    stdout = "All good!\nTask complete."
    process = _make_process(stdout)

    with (
        patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process),
        patch("ralph_runner._check_commit_since", new=AsyncMock(return_value=False)),
    ):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Fix the bug",
            job_id=10,
            repo_name="test-repo",
            supabase=mock_supabase,
            job_started_at=datetime.now(timezone.utc),
            commit_expected=True,
        )

    assert result["success"] is False
    assert result["gate1"] is not None
    assert result["gate1"]["passed"] is False
    assert "ghost success" in result["summary"].lower() or "no commit" in result["summary"].lower()


@pytest.mark.asyncio
async def test_gate1_passes_when_commit_exists(mock_supabase, tmp_path):
    """ARCH-021 Gate 1: commit present → gate1.passed=True, success unchanged."""
    stdout = "Done!\nAll tests pass."
    process = _make_process(stdout)

    with (
        patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process),
        patch("ralph_runner._check_commit_since", new=AsyncMock(return_value=True)),
        # Gate 2 skipped — no decision_text
    ):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Fix the bug",
            job_id=11,
            repo_name="test-repo",
            supabase=mock_supabase,
            job_started_at=datetime.now(timezone.utc),
            commit_expected=True,
        )

    assert result["success"] is True
    assert result["gate1"]["passed"] is True


@pytest.mark.asyncio
async def test_gate1_skipped_when_commit_not_expected(mock_supabase, tmp_path):
    """ARCH-021 Gate 1: commit_expected=False → gate1 is None, no git check."""
    stdout = "Done!"
    process = _make_process(stdout)
    check_mock = AsyncMock(return_value=False)

    with (
        patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process),
        patch("ralph_runner._check_commit_since", new=check_mock),
    ):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Read-only analysis",
            job_id=12,
            repo_name="test-repo",
            supabase=mock_supabase,
            commit_expected=False,
        )

    assert result["success"] is True
    assert result["gate1"] is None
    check_mock.assert_not_called()


@pytest.mark.asyncio
async def test_gate2_semantic_drift_fails_job(mock_supabase, tmp_path):
    """ARCH-021 Gate 2: Haiku returns aligned=False with confidence>=5 → success=False."""
    stdout = "Done! Changes look good."
    process = _make_process(stdout)
    alignment = {"aligned": False, "confidence": 7, "mismatches": ["wrong file changed"]}

    with (
        patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process),
        patch("ralph_runner._check_commit_since", new=AsyncMock(return_value=True)),
        patch("ralph_runner._check_intent_alignment", new=AsyncMock(return_value=alignment)),
    ):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Fix login bug in auth.ts",
            job_id=13,
            repo_name="test-repo",
            supabase=mock_supabase,
            job_started_at=datetime.now(timezone.utc),
            decision_text="Fix login bug in auth.ts",
            commit_expected=True,
        )

    assert result["success"] is False
    assert result["gate2"]["passed"] is False
    assert "wrong file changed" in result["summary"]


@pytest.mark.asyncio
async def test_gate2_fails_open_on_api_unavailable(mock_supabase, tmp_path):
    """ARCH-021 Gate 2: API unavailable (confidence=-1) → job not blocked."""
    stdout = "Done!"
    process = _make_process(stdout)
    alignment = {"aligned": False, "confidence": -1, "mismatches": [], "note": "api_key_missing"}

    with (
        patch("ralph_runner.asyncio.create_subprocess_exec", return_value=process),
        patch("ralph_runner._check_commit_since", new=AsyncMock(return_value=True)),
        patch("ralph_runner._check_intent_alignment", new=AsyncMock(return_value=alignment)),
    ):
        result = await ralph_runner.run_claude(
            repo_path=str(tmp_path),
            prompt_text="Fix the bug",
            job_id=14,
            repo_name="test-repo",
            supabase=mock_supabase,
            job_started_at=datetime.now(timezone.utc),
            decision_text="Fix the bug",
            commit_expected=True,
        )

    # API unavailable → fail open → job succeeds
    assert result["success"] is True
    assert result["gate2"]["passed"] is True
