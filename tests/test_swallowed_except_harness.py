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


def test_module_counter_window_catches_slow_cadence_failures():
    """The PRODUCTION singleton's prune window must be wide enough that a
    ~30-min-cadence in-loop task failing every run accumulates >= threshold.

    Reproduces the feature_health blind spot: the escalation check runs every
    ~30 min (1800 s), so with a 600 s window each failure is pruned before the
    next one fires and the count never exceeds 1 -> never escalates. The window
    must cover check_interval x threshold (>= 5400 s).
    """
    from nervous_system.swallowed_except_harness import counter

    now = time.monotonic()
    try:
        # Three failures 30 min apart — a slow task erroring on each iteration.
        counter._counts["slow_task"] = [now - 3600, now - 1800, now - 5]
        assert counter.count("slow_task") >= 3
    finally:
        counter._counts.pop("slow_task", None)


@pytest.mark.asyncio
async def test_check_escalation_applies_window_param_to_counter():
    """The window_seconds param must actually drive pruning (it was dead:
    prune/count read the counter's own field, ignoring the param)."""
    from nervous_system.swallowed_except_harness import SwallowedExceptCounter

    c = SwallowedExceptCounter(window_seconds=600)
    with patch("nervous_system.swallowed_except_harness.counter", c), \
         patch("nervous_system.swallowed_except_harness.get_chat_id", return_value=None):
        await check_swallowed_escalation(MagicMock(), AsyncMock(), window_seconds=9999)

    assert c.window_seconds == 9999


def test_alert_text_states_the_real_window():
    """The surfaced alert must state its actual window, not a hardcoded 'last
    hour' that lies once the window is widened."""
    from nervous_system.swallowed_except_harness import (
        _alert_text,
        SWALLOWED_PRUNE_WINDOW_S,
    )

    txt = _alert_text("broken_thing", 4, SWALLOWED_PRUNE_WINDOW_S)
    assert "broken_thing" in txt
    assert "4x" in txt
    assert f"{SWALLOWED_PRUNE_WINDOW_S // 3600}h" in txt
    assert "last hour" not in txt


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
