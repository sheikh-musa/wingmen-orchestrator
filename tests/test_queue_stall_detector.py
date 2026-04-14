"""Tests for queue stall detector."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from nervous_system.queue_stall_detector import check_queue_stalls


def _make_job(job_id, minutes_ago, priority=1, description="Build the widget"):
    """Helper to create a mock job dict with created_at N minutes ago."""
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": job_id,
        "repo_name": "test-repo",
        "description": description,
        "priority": priority,
        "created_at": created,
    }


def _mock_supabase(jobs, dedup_exists=False):
    """Build a supabase mock that routes table calls."""
    supabase = MagicMock()

    jobs_select_execute = AsyncMock(return_value=MagicMock(data=jobs))
    dedup_data = [{"id": "existing"}] if dedup_exists else []
    dedup_execute = AsyncMock(return_value=MagicMock(data=dedup_data))
    insert_execute = AsyncMock(return_value=MagicMock(data=[]))
    dedup_rows_execute = AsyncMock(return_value=MagicMock(data=[]))

    def table_router(table_name):
        mock_table = MagicMock()
        if table_name == "jobs":
            select_mock = MagicMock()

            def eq_router(field, value):
                result_mock = MagicMock()
                if field == "status" and value == "queued":
                    result_mock.execute = jobs_select_execute
                return result_mock

            select_mock.eq = eq_router
            mock_table.select.return_value = select_mock

        elif table_name == "notification_log":
            def notif_select_router(cols):
                if cols == "id":
                    chain = MagicMock()
                    chain.eq.return_value.limit.return_value.execute = dedup_execute
                    return chain
                else:
                    chain = MagicMock()
                    chain.eq.return_value.execute = dedup_rows_execute
                    return chain

            mock_table.select = notif_select_router
            mock_table.insert.return_value.execute = insert_execute

        return mock_table

    supabase.table = MagicMock(side_effect=table_router)
    return supabase


@pytest.mark.asyncio
async def test_stalled_job_sends_alert():
    """Job queued for 45 minutes -> alert sent to CTO."""
    supabase = _mock_supabase([_make_job(42, 45)])
    bot = AsyncMock()

    with patch("nervous_system.queue_stall_detector.get_chat_id", return_value="cto_chat"):
        await check_queue_stalls(supabase, bot)

    assert bot.send_message.call_count == 1
    call_kwargs = bot.send_message.call_args_list[0].kwargs
    assert call_kwargs["chat_id"] == "cto_chat"
    assert "42" in call_kwargs["text"]
    assert "test-repo" in call_kwargs["text"]
    assert "/cancel" in call_kwargs["text"]
    assert "\u23f3" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_recent_job_no_alert():
    """Job queued 10 minutes ago -> no alert sent."""
    supabase = _mock_supabase([_make_job(10, 10)])
    bot = AsyncMock()

    with patch("nervous_system.queue_stall_detector.get_chat_id", return_value="cto_chat"):
        await check_queue_stalls(supabase, bot)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_dedup_prevents_duplicate_alert():
    """If notification_log already has the dedup_key, skip sending."""
    supabase = _mock_supabase([_make_job(42, 45)], dedup_exists=True)
    bot = AsyncMock()

    with patch("nervous_system.queue_stall_detector.get_chat_id", return_value="cto_chat"):
        await check_queue_stalls(supabase, bot)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_no_cto_chat_id_returns_silently():
    """get_chat_id returns None -> no crash, no sends."""
    supabase = _mock_supabase([_make_job(42, 45)])
    bot = AsyncMock()

    with patch("nervous_system.queue_stall_detector.get_chat_id", return_value=None):
        await check_queue_stalls(supabase, bot)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_clears_dedup():
    """Dedup key exists for job no longer queued -> dedup record deleted."""
    deleted_ids = []

    supabase = MagicMock()
    jobs_execute = AsyncMock(return_value=MagicMock(data=[]))
    dedup_rows_data = [{"id": "row-1", "dedup_key": "queue_stall:99"}]
    dedup_rows_execute = AsyncMock(return_value=MagicMock(data=dedup_rows_data))
    job_status_execute = AsyncMock(return_value=MagicMock(data=[{"status": "running"}]))

    async def capture_delete(*args, **kwargs):
        return MagicMock(data=[])

    delete_execute = AsyncMock(side_effect=capture_delete)

    def table_router(table_name):
        mock_table = MagicMock()
        if table_name == "jobs":
            select_mock = MagicMock()

            def eq_router(field, value):
                m = MagicMock()
                if field == "status":
                    m.execute = jobs_execute
                else:
                    m.limit.return_value.execute = job_status_execute
                return m

            select_mock.eq = eq_router
            mock_table.select.return_value = select_mock
        elif table_name == "notification_log":
            def notif_select(cols):
                m = MagicMock()
                if cols == "id":
                    m.eq.return_value.limit.return_value.execute = AsyncMock(
                        return_value=MagicMock(data=[])
                    )
                else:
                    m.eq.return_value.execute = dedup_rows_execute
                return m

            mock_table.select = notif_select
            mock_table.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            mock_table.delete.return_value.eq.return_value.execute = delete_execute
        return mock_table

    supabase.table = MagicMock(side_effect=table_router)
    bot = AsyncMock()

    with patch("nervous_system.queue_stall_detector.get_chat_id", return_value="cto_chat"):
        await check_queue_stalls(supabase, bot)

    bot.send_message.assert_not_called()
    assert delete_execute.call_count == 1
