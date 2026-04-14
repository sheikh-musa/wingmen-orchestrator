"""Tests for swallowed exception harness."""

import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nervous_system.swallowed_except_harness import (
    SwallowedExceptCounter,
    check_swallowed_escalation,
    record_swallowed,
)


def test_counter_records_and_counts():
    """Record 3 events for a label, assert count == 3."""
    c = SwallowedExceptCounter()
    c.record("test_label")
    c.record("test_label")
    c.record("test_label")
    assert c.count("test_label") == 3


def test_counter_prunes_old_entries():
    """Manually backdate timestamps, assert pruning works."""
    c = SwallowedExceptCounter(window_seconds=60)
    now = time.monotonic()
    c._counts["old_label"] = [now - 120, now - 90, now - 10]
    assert c.count("old_label") == 1


@pytest.mark.asyncio
async def test_escalation_fires_above_threshold():
    """Record 5+ events, mock supabase with no existing dedup -> alert sent."""
    c = SwallowedExceptCounter()
    for _ in range(6):
        c.record("broken_thing")

    supabase = MagicMock()

    dedup_execute = AsyncMock(return_value=MagicMock(data=[]))
    insert_execute = AsyncMock(return_value=MagicMock(data=[]))
    recovery_execute = AsyncMock(return_value=MagicMock(data=[]))

    def table_router(table_name):
        mock_table = MagicMock()
        if table_name == "notification_log":
            def select_router(cols):
                chain = MagicMock()
                if cols == "id":
                    chain.eq.return_value.limit.return_value.execute = dedup_execute
                else:
                    chain.eq.return_value.execute = recovery_execute
                return chain
            mock_table.select = select_router
            mock_table.insert.return_value.execute = insert_execute
        return mock_table

    supabase.table = MagicMock(side_effect=table_router)
    bot = AsyncMock()

    with patch("nervous_system.swallowed_except_harness.counter", c), \
         patch("nervous_system.swallowed_except_harness.get_chat_id", return_value="cto_chat"):
        await check_swallowed_escalation(supabase, bot)

    assert bot.send_message.call_count == 1
    call_kwargs = bot.send_message.call_args_list[0].kwargs
    assert call_kwargs["chat_id"] == "cto_chat"
    assert "broken_thing" in call_kwargs["text"]
    assert "6x" in call_kwargs["text"]
    assert insert_execute.call_count == 1


@pytest.mark.asyncio
async def test_escalation_dedup_prevents_repeat():
    """Existing dedup record -> no alert sent."""
    c = SwallowedExceptCounter()
    for _ in range(6):
        c.record("dup_label")

    supabase = MagicMock()

    dedup_execute = AsyncMock(return_value=MagicMock(data=[{"id": "existing"}]))
    recovery_execute = AsyncMock(return_value=MagicMock(data=[]))

    def table_router(table_name):
        mock_table = MagicMock()
        if table_name == "notification_log":
            def select_router(cols):
                chain = MagicMock()
                if cols == "id":
                    chain.eq.return_value.limit.return_value.execute = dedup_execute
                else:
                    chain.eq.return_value.execute = recovery_execute
                return chain
            mock_table.select = select_router
        return mock_table

    supabase.table = MagicMock(side_effect=table_router)
    bot = AsyncMock()

    with patch("nervous_system.swallowed_except_harness.counter", c), \
         patch("nervous_system.swallowed_except_harness.get_chat_id", return_value="cto_chat"):
        await check_swallowed_escalation(supabase, bot)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_sweep_clears_stale_dedup():
    """Existing dedup records with count below threshold//2 -> DELETE called."""
    c = SwallowedExceptCounter()
    # Don't record anything — count for "recovered_label" is 0, below threshold//2 (2)

    supabase = MagicMock()

    dedup_rows = [{"id": "row-1", "dedup_key": "swallowed_except:recovered_label"}]
    recovery_execute = AsyncMock(return_value=MagicMock(data=dedup_rows))
    delete_execute = AsyncMock(return_value=MagicMock(data=[]))

    def table_router(table_name):
        mock_table = MagicMock()
        if table_name == "notification_log":
            def select_router(cols):
                chain = MagicMock()
                if cols == "id":
                    chain.eq.return_value.limit.return_value.execute = AsyncMock(
                        return_value=MagicMock(data=[])
                    )
                else:
                    chain.eq.return_value.execute = recovery_execute
                return chain
            mock_table.select = select_router
            mock_table.delete.return_value.eq.return_value.execute = delete_execute
        return mock_table

    supabase.table = MagicMock(side_effect=table_router)
    bot = AsyncMock()

    with patch("nervous_system.swallowed_except_harness.counter", c), \
         patch("nervous_system.swallowed_except_harness.get_chat_id", return_value="cto_chat"):
        await check_swallowed_escalation(supabase, bot)

    bot.send_message.assert_not_called()
    assert delete_execute.call_count == 1
