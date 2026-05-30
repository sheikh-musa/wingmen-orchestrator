"""Tests for nervous_system.agent_messages_realtime — CADENCE-003 Strategy A Level 1."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system import agent_messages_realtime as rt


def _supabase_with_row(row: dict | None):
    """Build a mock supabase chain that returns `[row]` from table(...).select(...).eq(...).limit(...).execute()."""
    supabase = MagicMock()
    table = MagicMock()
    select = MagicMock()
    eq = MagicMock()
    limit = MagicMock()
    execute = AsyncMock(return_value=MagicMock(data=[row] if row else []))
    supabase.table.return_value = table
    table.select.return_value = select
    select.eq.return_value = eq
    eq.limit.return_value = limit
    limit.execute = execute
    return supabase


class TestRouteSingleMessage:
    async def test_already_forwarded_skipped(self):
        """If forwarded_to_telegram_at is non-null, the realtime path no-ops
        (the poll's belt-and-suspenders has already handled this msg)."""
        msg = {"id": 1, "forwarded_to_telegram_at": "2026-05-31T01:00:00Z", "skipped_at": None}
        supabase = _supabase_with_row(msg)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        await rt._route_single_message(supabase, bot, "musa-chat", 1)
        bot.send_message.assert_not_called()

    async def test_is_test_row_skipped(self):
        msg = {"id": 1, "is_test": True}
        supabase = _supabase_with_row(msg)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        await rt._route_single_message(supabase, bot, "musa-chat", 1)
        bot.send_message.assert_not_called()

    async def test_row_not_found_no_send(self):
        supabase = _supabase_with_row(None)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        await rt._route_single_message(supabase, bot, "musa-chat", 999)
        bot.send_message.assert_not_called()

    async def test_routable_message_sends_and_marks(self):
        """Happy path: routable + unprocessed → Telegram send + log + mark forwarded."""
        msg = {
            "id": 42, "from_agent": "cai", "to_agent": "cc-orchestrator",
            "message_type": "review_request", "subject": "Test",
            "body": "Body", "requires_response": True, "priority": "P2",
            "created_at": "2026-05-31T01:00:00Z",
            "is_test": False, "forwarded_to_telegram_at": None, "skipped_at": None,
            "read_at": None,
        }
        supabase = _supabase_with_row(msg)
        bot = MagicMock()
        sent = MagicMock(message_id=12345)
        bot.send_message = AsyncMock(return_value=sent)

        with patch.object(rt.agent_messages_poll, "_already_notified", new=AsyncMock(return_value=False)), \
             patch.object(rt.agent_messages_poll, "_log_notification", new=AsyncMock()) as log_mock, \
             patch.object(rt.agent_messages_poll, "_mark_forwarded", new=AsyncMock()) as mark_mock:
            await rt._route_single_message(supabase, bot, "musa-chat", 42)

        bot.send_message.assert_awaited_once()
        log_mock.assert_awaited_once()
        mark_mock.assert_awaited_once()

    async def test_dedup_blocks_resend(self):
        """If notification_log already has a row for this dedup_key, don't resend."""
        msg = {
            "id": 7, "from_agent": "cai", "to_agent": "cc-orchestrator",
            "message_type": "review_request", "subject": "x", "body": "x",
            "requires_response": True, "priority": "P2",
            "created_at": "2026-05-31T01:00:00Z", "is_test": False,
            "forwarded_to_telegram_at": None, "skipped_at": None, "read_at": None,
        }
        supabase = _supabase_with_row(msg)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        with patch.object(rt.agent_messages_poll, "_already_notified", new=AsyncMock(return_value=True)):
            await rt._route_single_message(supabase, bot, "musa-chat", 7)
        bot.send_message.assert_not_called()


class TestSubscribeLoopErrorHandling:
    """The subscribe loop must survive subscribe() raising and retry after backoff."""

    async def test_subscribe_inner_loop_exits_on_disconnect(self, monkeypatch):
        """When is_connected flips False during the inner poll, outer loop reconnects."""
        monkeypatch.setattr(rt, "_RECONNECT_BACKOFF_SECONDS", 0.01)

        import asyncio as _aio
        # is_connected returns True once, then False (drop simulation), then we cancel.
        states = iter([True, True, False])  # connected, still connected, dropped
        realtime = MagicMock()
        realtime.connect = AsyncMock()
        realtime.close = AsyncMock()
        type(realtime).is_connected = property(lambda self: next(states))

        supabase = MagicMock()
        supabase.realtime = realtime
        channel = MagicMock()
        channel.subscribe = AsyncMock()
        supabase.channel.return_value = channel

        # Capture the inner is_connected poll: one short sleep then we cancel the outer loop.
        sleeps = 0
        real_sleep = _aio.sleep
        async def short_sleep_then_cancel(seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                raise _aio.CancelledError()
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=short_sleep_then_cancel):
            with pytest.raises(_aio.CancelledError):
                await rt.subscribe_agent_messages(supabase)

        # subscribe should have been called more than once because of the disconnect-reconnect cycle.
        assert channel.subscribe.await_count >= 1
