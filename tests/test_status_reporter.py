"""Tests for status_reporter.py — formatting, notifications, STATUS.md."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import mock_supabase_chain


class TestFormatElapsed:
    def test_seconds_only(self):
        from status_reporter import _format_elapsed

        assert _format_elapsed(45) == "45s"

    def test_minutes_and_seconds(self):
        from status_reporter import _format_elapsed

        assert _format_elapsed(125) == "2m 5s"

    def test_zero(self):
        from status_reporter import _format_elapsed

        assert _format_elapsed(0) == "0s"


class TestNotifyProgress:
    @pytest.mark.asyncio
    async def test_sends_to_admin(self):
        from status_reporter import notify_progress

        sb = mock_supabase_chain([])

        mock_resp = AsyncMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notify_progress(1, "ihsandms", "picked", "Starting build", supabase=sb)

        mock_client.post.assert_called()
        call_args = mock_client.post.call_args
        assert "sendMessage" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_skips_without_token(self, monkeypatch):
        from status_reporter import notify_progress

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notify_progress(1, "ihsandms", "picked", "test")

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_skips_duplicate(self):
        from status_reporter import notify_progress

        existing = [{"id": 99}]
        sb = mock_supabase_chain(existing)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notify_progress(1, "ihsandms", "picked", "test", supabase=sb)

        mock_client.post.assert_not_called()


class TestUpdateStatusMd:
    @pytest.mark.asyncio
    async def test_writes_fresh_status(self, tmp_path):
        from status_reporter import _update_status_md

        job = {
            "id": 42,
            "repo_name": "ihsandms",
            "description": "Add login page",
            "result_summary": "Completed successfully",
        }
        await _update_status_md(tmp_path, job, "green", "https://test.vercel.app", "2026-04-14 12:00 SGT")

        status_file = tmp_path / "STATUS.md"
        assert status_file.exists()
        content = status_file.read_text()
        assert "ihsandms STATUS" in content
        assert "green" in content
        assert "https://test.vercel.app" in content
        assert "Job #42" in content

    @pytest.mark.asyncio
    async def test_preserves_next_up_section(self, tmp_path):
        from status_reporter import _update_status_md

        status_file = tmp_path / "STATUS.md"
        status_file.write_text("# Old\n## Next Up\n- Fix bug\n- Add tests\n## Other\nstuff\n")

        job = {
            "id": 43,
            "repo_name": "ihsandms",
            "description": "Refactor auth",
            "result_summary": "Done",
        }
        await _update_status_md(tmp_path, job, "green", None, "2026-04-14 13:00 SGT")

        content = status_file.read_text()
        assert "Previous Next Up" in content
        assert "Fix bug" in content
