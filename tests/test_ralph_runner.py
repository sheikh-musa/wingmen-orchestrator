"""Tests for ralph_runner module (mocked subprocess)."""

import asyncio
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
