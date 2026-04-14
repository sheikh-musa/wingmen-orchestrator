"""Tests for test_gate.run_tests()."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import mock_supabase_chain
import test_gate


@pytest.fixture
def tmp_repo(tmp_path):
    return str(tmp_path)


@pytest.fixture
def supabase():
    return mock_supabase_chain()


@pytest.mark.asyncio
async def test_skips_when_no_test_infrastructure(tmp_repo, supabase):
    result = await test_gate.run_tests(tmp_repo, "test-repo", 1, supabase)
    assert result["passed"] is True
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_detects_pytest(tmp_repo, supabase):
    Path(tmp_repo, "pytest.ini").write_text("[pytest]\n")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"1 passed", b""))
    mock_proc.returncode = 0

    with patch("test_gate.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await test_gate.run_tests(tmp_repo, "test-repo", 1, supabase)
        cmd_args = mock_exec.call_args[0]
        assert "pytest" in " ".join(cmd_args)
    assert result["passed"] is True
    assert result["skipped"] is False


@pytest.mark.asyncio
async def test_detects_npm_test(tmp_repo, supabase):
    pkg = {"scripts": {"test": "jest"}}
    Path(tmp_repo, "package.json").write_text(json.dumps(pkg))

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"Tests passed", b""))
    mock_proc.returncode = 0

    with patch("test_gate.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await test_gate.run_tests(tmp_repo, "test-repo", 1, supabase)
        cmd_args = mock_exec.call_args[0]
        assert "npm" in cmd_args
    assert result["passed"] is True
    assert result["skipped"] is False


@pytest.mark.asyncio
async def test_handles_test_timeout(tmp_repo, supabase):
    Path(tmp_repo, "pytest.ini").write_text("[pytest]\n")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch("test_gate.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await test_gate.run_tests(tmp_repo, "test-repo", 1, supabase)
    assert result["passed"] is False
    assert "timed out" in result["output"].lower()
    assert result["skipped"] is False


@pytest.mark.asyncio
async def test_handles_test_failure(tmp_repo, supabase):
    Path(tmp_repo, "pytest.ini").write_text("[pytest]\n")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"FAILED test_foo.py", b""))
    mock_proc.returncode = 1

    with patch("test_gate.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await test_gate.run_tests(tmp_repo, "test-repo", 1, supabase)
    assert result["passed"] is False
    assert result["skipped"] is False
