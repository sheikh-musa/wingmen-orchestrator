"""Tests for build_audit.maybe_audit()."""

from unittest.mock import patch, AsyncMock

import pytest

from tests.conftest import mock_supabase_chain
import build_audit


@pytest.fixture
def supabase():
    return mock_supabase_chain()


@pytest.mark.asyncio
async def test_skips_most_builds(supabase, tmp_path):
    with patch("build_audit.random.random", return_value=0.5):
        await build_audit.maybe_audit(str(tmp_path), 1, "test-repo", supabase)
    supabase.table.assert_not_called()


@pytest.mark.asyncio
async def test_runs_audit_on_low_roll(supabase, tmp_path):
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"file.py | 5 +++--", b""))
    mock_proc.returncode = 0

    with patch("build_audit.random.random", return_value=0.1), \
         patch("build_audit.asyncio.create_subprocess_exec", return_value=mock_proc):
        await build_audit.maybe_audit(str(tmp_path), 1, "test-repo", supabase)
    supabase.table.assert_called()


@pytest.mark.asyncio
async def test_never_raises(supabase, tmp_path):
    with patch("build_audit.random.random", return_value=0.1), \
         patch("build_audit.asyncio.create_subprocess_exec", side_effect=RuntimeError("boom")):
        await build_audit.maybe_audit(str(tmp_path), 1, "test-repo", supabase)
