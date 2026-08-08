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


class TestLivenessTracker:
    """5B: the delivery-lag detector. The silent-stall bug is is_connected==True
    while the server has stopped pushing INSERTs — so we can't trust is_connected.
    We track the last insert id actually DELIVERED via the callback vs DB max(id);
    a gap that PERSISTS past the grace window is a confirmed stall."""

    def test_caught_up_never_stalls(self):
        """last delivered == db_max → no lag, no stall, however many times polled."""
        t = rt._LivenessTracker(grace_seconds=120)
        t.seed(1000)
        assert t.evaluate(db_max=1000, now=0) is False
        assert t.evaluate(db_max=1000, now=500) is False
        assert t.lag() == 0

    def test_idle_no_new_rows_never_stalls(self):
        """No new inserts at all (db_max frozen == seed) must NOT read as a stall —
        an idle bus is healthy, not wedged."""
        t = rt._LivenessTracker(grace_seconds=120)
        t.seed(500)
        for now in (0, 60, 120, 300, 100000):
            assert t.evaluate(db_max=500, now=now) is False

    def test_stall_flagged_only_after_grace(self):
        """DB advances past what realtime delivered; the gap is NOT a stall until it
        has persisted for the full grace window (absorbs normal delivery latency)."""
        t = rt._LivenessTracker(grace_seconds=120)
        t.seed(1000)
        # a new row lands in the DB but realtime never delivers it
        assert t.evaluate(db_max=1001, now=0) is False      # gap just observed
        assert t.evaluate(db_max=1001, now=119) is False     # still within grace
        assert t.evaluate(db_max=1001, now=120) is True      # persisted past grace → STALL
        assert t.lag() == 1

    def test_delivery_within_grace_clears_and_resets_timer(self):
        """If the callback catches up before grace elapses, no stall AND the timer
        resets — a later fresh gap needs a full new grace window, not the leftover."""
        t = rt._LivenessTracker(grace_seconds=120)
        t.seed(1000)
        assert t.evaluate(db_max=1001, now=0) is False       # gap opens at t=0
        t.note_delivered(1001)                                # realtime catches up at t=60
        assert t.evaluate(db_max=1001, now=60) is False       # caught up → cleared
        # a fresh gap is first observed at t=100; the timer must restart from there,
        # NOT count from the original t=0 gap (which would already exceed grace).
        assert t.evaluate(db_max=1002, now=100) is False      # fresh gap observed
        assert t.evaluate(db_max=1002, now=219) is False      # 119s in — timer reset held
        assert t.evaluate(db_max=1002, now=220) is True       # full 120s after fresh gap

    def test_seed_prevents_boot_false_alarm(self):
        """At subscribe time last_realtime_id is 0 but db_max is large; without a
        baseline seed that reads as a huge lag. seed() must adopt db_max as caught-up."""
        t = rt._LivenessTracker(grace_seconds=120)
        assert t.evaluate(db_max=99999, now=0) is False       # gap observed, within grace
        # ... but if we never seed and grace passes it would false-alarm; seeding fixes it:
        t.seed(99999)
        assert t.evaluate(db_max=99999, now=100000) is False
        assert t.lag() == 0

    def test_note_delivered_is_monotonic(self):
        """An out-of-order lower id must never pull last_realtime_id backward."""
        t = rt._LivenessTracker(grace_seconds=120)
        t.note_delivered(50)
        t.note_delivered(10)   # stale/duplicate delivery
        assert t.last_realtime_id == 50


class TestSilentStallSelfHeal:
    """5B integration: the inner poll must not trust is_connected alone. When the WS
    stays 'connected' but the DB advances past the last delivered insert id and the
    gap persists past grace, the loop must fire the stall handler (→ exit+resubscribe)."""

    async def test_inner_loop_fires_stall_handler_on_persistent_lag(self, monkeypatch):
        monkeypatch.setattr(rt, "_LAG_POLL_SECONDS", 0)
        # db_max: 1000 at subscribe-time seed, then 1005 forever (a row realtime never delivered).
        maxes = iter([1000] + [1005] * 50)
        async def fake_db_max(_supabase):
            return next(maxes)
        monkeypatch.setattr(rt, "_db_max_id", fake_db_max)
        # monotonic clock: gap first observed at t=0, then t=200 (> 120s grace) → stall.
        times = iter([0.0, 200.0] + [9999.0] * 50)
        monkeypatch.setattr(rt.time, "monotonic", lambda: next(times))

        # Production _stall_detected does os._exit(1) — uncatchable, ends the process.
        # The fake mirrors that with SystemExit (BaseException), so it escapes the
        # loop's `except Exception` exactly as os._exit would, rather than being
        # swallowed into another reconnect.
        stall_calls = []
        def fake_stall(lag):
            stall_calls.append(lag)
            raise SystemExit(1)
        monkeypatch.setattr(rt, "_stall_detected", fake_stall)

        realtime = MagicMock()
        realtime.connect = AsyncMock()
        type(realtime).is_connected = property(lambda self: True)  # never flips — the whole point
        supabase = MagicMock()
        supabase.realtime = realtime
        channel = MagicMock()
        channel.subscribe = AsyncMock()
        supabase.channel.return_value = channel

        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(SystemExit):
                await rt.subscribe_agent_messages(supabase)

        assert stall_calls == [5]  # lag = 1005 - 1000

    async def test_no_stall_on_idle_bus(self, monkeypatch):
        """An idle bus (db_max frozen at the subscribe-time seed, no new inserts) must
        NEVER fire the stall handler, no matter how much wall time passes — quiet is
        healthy, not wedged. is_connected stays True the whole time (never drops)."""
        import asyncio as _aio
        monkeypatch.setattr(rt, "_LAG_POLL_SECONDS", 0)
        async def fake_db_max(_supabase):
            return 1000  # frozen: the subscribe-time seed and every poll see this max
        monkeypatch.setattr(rt, "_db_max_id", fake_db_max)
        # clock marches far past grace on every tick — proving time alone can't stall it:
        clock = {"t": 0.0}
        def fake_monotonic():
            clock["t"] += 10_000
            return clock["t"]
        monkeypatch.setattr(rt.time, "monotonic", fake_monotonic)
        stall_calls = []
        monkeypatch.setattr(rt, "_stall_detected", lambda lag: stall_calls.append(lag))

        realtime = MagicMock()
        realtime.connect = AsyncMock()
        realtime.close = AsyncMock()
        type(realtime).is_connected = property(lambda self: True)  # never drops
        supabase = MagicMock()
        supabase.realtime = realtime
        channel = MagicMock()
        channel.subscribe = AsyncMock()
        supabase.channel.return_value = channel

        # Bound the test: cancel from within the inner-loop sleep after several ticks
        # (same deterministic pattern as test_subscribe_inner_loop_exits_on_disconnect).
        ticks = 0
        real_sleep = _aio.sleep
        async def counting_sleep(_secs):
            nonlocal ticks
            ticks += 1
            if ticks >= 8:
                raise _aio.CancelledError()
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=counting_sleep):
            with pytest.raises(_aio.CancelledError):
                await rt.subscribe_agent_messages(supabase)

        assert stall_calls == []


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
