"""Tests for paused job escalation scheduler."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from nervous_system.paused_job_escalation import check_paused_jobs


def _make_job(job_id, hours_ago, fail_count=1, result_summary="some error"):
    """Helper to create a mock job dict with updated_at N hours ago."""
    updated = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {
        "id": job_id,
        "repo_name": "test-repo",
        "description": "Build the widget",
        "fail_count": fail_count,
        "result_summary": result_summary,
        "updated_at": updated,
    }


def _mock_supabase(jobs, dedup_exists=False):
    """Build a supabase mock that returns jobs for the jobs query and handles notification_log."""
    supabase = MagicMock()

    jobs_execute = AsyncMock(return_value=MagicMock(data=jobs))
    dedup_data = [{"id": "existing"}] if dedup_exists else []
    dedup_execute = AsyncMock(return_value=MagicMock(data=dedup_data))
    insert_execute = AsyncMock(return_value=MagicMock(data=[]))

    def table_router(table_name):
        mock_table = MagicMock()
        if table_name == "jobs":
            mock_table.select.return_value.eq.return_value.execute = jobs_execute
        elif table_name == "notification_log":
            mock_table.select.return_value.eq.return_value.limit.return_value.execute = dedup_execute
            mock_table.insert.return_value.execute = insert_execute
        return mock_table

    supabase.table = MagicMock(side_effect=table_router)
    return supabase


@pytest.mark.asyncio
async def test_one_hour_paused_sends_reminder():
    """Job paused for 2 hours -> reminder sent to CTO."""
    supabase = _mock_supabase([_make_job(42, 2.0)])
    bot = AsyncMock()

    with patch("nervous_system.paused_job_escalation.get_chat_id", return_value="cto_chat"):
        await check_paused_jobs(supabase, bot)

    assert bot.send_message.call_count == 1
    call_kwargs = bot.send_message.call_args_list[0].kwargs
    assert call_kwargs["chat_id"] == "cto_chat"
    assert "42" in call_kwargs["text"]
    assert "test-repo" in call_kwargs["text"]
    assert "/resume" in call_kwargs["text"]
    assert "\U0001f534" not in call_kwargs["text"]


@pytest.mark.asyncio
async def test_six_hour_paused_sends_urgent():
    """Job paused for 8 hours -> urgent escalation to CTO."""
    supabase = _mock_supabase([_make_job(99, 8.0, fail_count=3, result_summary="timeout error")])
    bot = AsyncMock()

    with patch("nervous_system.paused_job_escalation.get_chat_id", return_value="cto_chat"):
        await check_paused_jobs(supabase, bot)

    assert bot.send_message.call_count == 1
    call_kwargs = bot.send_message.call_args_list[0].kwargs
    assert call_kwargs["chat_id"] == "cto_chat"
    assert "99" in call_kwargs["text"]
    assert "\U0001f534" in call_kwargs["text"]
    assert "timeout error" in call_kwargs["text"]
    assert "/cancel" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_recent_pause_no_notification():
    """Job paused 30 minutes ago -> no notification."""
    supabase = _mock_supabase([_make_job(10, 0.5)])
    bot = AsyncMock()

    with patch("nervous_system.paused_job_escalation.get_chat_id", return_value="cto_chat"):
        await check_paused_jobs(supabase, bot)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_dedup_prevents_duplicate():
    """If notification_log already has the dedup_key, skip sending."""
    supabase = _mock_supabase([_make_job(42, 2.0)], dedup_exists=True)
    bot = AsyncMock()

    with patch("nervous_system.paused_job_escalation.get_chat_id", return_value="cto_chat"):
        await check_paused_jobs(supabase, bot)

    bot.send_message.assert_not_called()
