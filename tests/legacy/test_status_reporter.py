"""Tests for status_reporter.py — formatting, notifications, STATUS.md."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import mock_supabase_chain

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



class TestFormatElapsed:
    def test_seconds_only(self):
        from legacy.status_reporter import _format_elapsed

        assert _format_elapsed(45) == "45s"

    def test_minutes_and_seconds(self):
        from legacy.status_reporter import _format_elapsed

        assert _format_elapsed(125) == "2m 5s"

    def test_zero(self):
        from legacy.status_reporter import _format_elapsed

        assert _format_elapsed(0) == "0s"


class TestNotifyProgress:
    @pytest.mark.asyncio
    async def test_sends_to_admin(self):
        from legacy.status_reporter import notify_progress

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
        from legacy.status_reporter import notify_progress

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notify_progress(1, "ihsandms", "picked", "test")

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_skips_duplicate(self):
        from legacy.status_reporter import notify_progress

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
        from legacy.status_reporter import _update_status_md

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
        assert "| #42 |" in content

    @pytest.mark.asyncio
    async def test_preserves_existing_content(self, tmp_path):
        from legacy.status_reporter import _update_status_md

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
        # Append-only: every line of hand-written content must survive
        assert "## Next Up" in content
        assert "- Fix bug" in content
        assert "- Add tests" in content
        assert "## Other" in content
        # And the marker section is appended below
        assert "## Recent Jobs (auto-tracked)" in content
        assert "| #43 |" in content


class TestStatusMdPreservation:

    @pytest.mark.asyncio
    async def test_preserves_content_above_marker(self, tmp_path):
        from legacy.status_reporter import _update_status_md

        status_file = tmp_path / "STATUS.md"
        hand_written = "# My Project\n\nImportant context here.\n\n## Architecture\nDetails about the system.\n"
        marker_section = "## Recent Jobs (auto-tracked)\n\nLast Updated: 2026-04-14 10:00 SGT\n\n| Job | Description | Status | Deploy |\n|-----|-------------|--------|--------|\n| #41 | Old job | green | N/A |\n"
        status_file.write_text(hand_written + "\n" + marker_section)

        job = {"id": 42, "repo_name": "test", "description": "New job", "result_summary": "Done"}
        await _update_status_md(tmp_path, job, "green", None, "2026-04-14 12:00 SGT")

        content = status_file.read_text()
        assert "# My Project" in content
        assert "Important context here." in content
        assert "## Architecture" in content
        assert "Details about the system." in content

    @pytest.mark.asyncio
    async def test_appends_marker_when_missing(self, tmp_path):
        from legacy.status_reporter import _update_status_md

        status_file = tmp_path / "STATUS.md"
        original = "# My Project\n\nHand-written content only.\n"
        status_file.write_text(original)

        job = {"id": 42, "repo_name": "test", "description": "New job", "result_summary": "Done"}
        await _update_status_md(tmp_path, job, "green", None, "2026-04-14 12:00 SGT")

        content = status_file.read_text()
        assert "# My Project" in content
        assert "Hand-written content only." in content
        assert "## Recent Jobs (auto-tracked)" in content
        assert "| #42 |" in content

    @pytest.mark.asyncio
    async def test_limits_job_rows_to_10(self, tmp_path):
        from legacy.status_reporter import _update_status_md

        status_file = tmp_path / "STATUS.md"
        existing_rows = "\n".join([f"| #{i} | Job {i} desc | green | N/A |" for i in range(1, 13)])
        content = (
            "# Project\n\n"
            "## Recent Jobs (auto-tracked)\n\n"
            "Last Updated: old\n\n"
            "| Job | Description | Status | Deploy |\n"
            "|-----|-------------|--------|--------|\n"
            f"{existing_rows}\n"
        )
        status_file.write_text(content)

        job = {"id": 99, "repo_name": "test", "description": "New job", "result_summary": "Done"}
        await _update_status_md(tmp_path, job, "green", None, "2026-04-14 12:00 SGT")

        result = status_file.read_text()
        job_rows = [line for line in result.splitlines() if line.startswith("| #")]
        assert len(job_rows) == 10
        assert "| #99 |" in result

    @pytest.mark.asyncio
    async def test_never_overwrites_entire_file(self, tmp_path):
        from legacy.status_reporter import _update_status_md

        status_file = tmp_path / "STATUS.md"
        lines = [f"Line {i}: important project context" for i in range(50)]
        original = "\n".join(lines) + "\n"
        status_file.write_text(original)

        job = {"id": 42, "repo_name": "test", "description": "New job", "result_summary": "Done"}
        await _update_status_md(tmp_path, job, "green", None, "2026-04-14 12:00 SGT")

        content = status_file.read_text()
        for i in range(50):
            assert f"Line {i}: important project context" in content
        assert "## Recent Jobs (auto-tracked)" in content
