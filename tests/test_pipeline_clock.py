"""Tests for pipeline_clock — TASK-042 days_clean increment."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

from nervous_system.pipeline_clock import (
    tick_pipeline_clock,
    _run_tick,
    _increment_clean,
    _handle_breach,
)
from tests.conftest import mock_supabase_chain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gate(gate_ref="fire_drill_migration", status="green", days_clean=0,
           required=14, id=1):
    return {
        "id": id,
        "gate_ref": gate_ref,
        "gate_title": f"Gate: {gate_ref}",
        "status": status,
        "days_clean": days_clean,
        "required_days_clean": required,
        "last_breach_at": None,
        "last_breach_reason": None,
        "phase": "phase_0",
    }


_NOW = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# tick_pipeline_clock — interval guard
# ---------------------------------------------------------------------------

class TestTickInterval:

    @pytest.mark.asyncio
    async def test_does_nothing_if_less_than_24h_elapsed(self):
        # last_run was 1 hour ago
        recent = (_NOW - timedelta(hours=1)).isoformat()
        config_row = [{"value": recent}]
        sb = mock_supabase_chain(config_row)

        with patch("nervous_system.pipeline_clock._run_tick", new_callable=AsyncMock) as mock_tick:
            await tick_pipeline_clock(sb, bot=None, musa_chat_id=None)
            mock_tick.assert_not_called()

    @pytest.mark.asyncio
    async def test_runs_if_24h_elapsed(self):
        # Use real now() so elapsed calculation inside tick_pipeline_clock is correct
        old_last_run = datetime.now(timezone.utc) - timedelta(hours=25)
        sb = MagicMock()

        with patch("nervous_system.pipeline_clock._get_last_run",
                   new=AsyncMock(return_value=old_last_run)):
            with patch("nervous_system.pipeline_clock._run_tick", new_callable=AsyncMock) as mock_tick:
                with patch("nervous_system.pipeline_clock._set_last_run", new_callable=AsyncMock):
                    await tick_pipeline_clock(sb)
                    mock_tick.assert_called_once()

    @pytest.mark.asyncio
    async def test_runs_if_no_prior_run(self):
        """First ever run (no config key) should tick immediately."""
        with patch("nervous_system.pipeline_clock._get_last_run",
                   new=AsyncMock(return_value=None)):
            with patch("nervous_system.pipeline_clock._run_tick", new_callable=AsyncMock) as mock_tick:
                with patch("nervous_system.pipeline_clock._set_last_run", new_callable=AsyncMock):
                    sb = MagicMock()
                    await tick_pipeline_clock(sb)
                    mock_tick.assert_called_once()


# ---------------------------------------------------------------------------
# _increment_clean
# ---------------------------------------------------------------------------

class TestIncrementClean:

    @pytest.mark.asyncio
    async def test_increments_days_clean_for_green_gate(self):
        gate = _gate(days_clean=3, required=14)
        sb = mock_supabase_chain([])  # execute returns empty (update)

        await _increment_clean(sb, _NOW, [gate])

        sb.update.assert_called()
        update_arg = sb.update.call_args[0][0]
        assert update_arg["days_clean"] == 4

    @pytest.mark.asyncio
    async def test_no_green_gates_does_nothing(self):
        sb = mock_supabase_chain([])
        await _increment_clean(sb, _NOW, green_gates=[])
        sb.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase_complete_notification_when_all_gates_reach_required(self):
        # Gate exactly at required-1 → increment to required → trigger completion
        gate = _gate(days_clean=13, required=14)

        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.update.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[]),  # update gate days_clean
            MagicMock(data=[]),  # notification_log dedup check (empty)
            MagicMock(data=[]),  # notification_log insert
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 55
        bot.send_message = AsyncMock(return_value=sent_mock)

        await _increment_clean(sb, _NOW, [gate], bot=bot, musa_chat_id="123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "Phase 0 complete" in text or "complete" in text.lower()

    @pytest.mark.asyncio
    async def test_no_phase_complete_if_some_gates_still_short(self):
        gates = [
            _gate(gate_ref="gate_a", days_clean=13, required=14, id=1),
            _gate(gate_ref="gate_b", days_clean=5, required=14, id=2),
        ]
        sb = mock_supabase_chain([])  # updates succeed
        bot = AsyncMock()

        await _increment_clean(sb, _NOW, gates, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_breach
# ---------------------------------------------------------------------------

class TestHandleBreach:

    @pytest.mark.asyncio
    async def test_resets_days_clean_and_sends_telegram(self):
        breach_gate = _gate(gate_ref="bad_gate", status="breach", id=1)
        green_gate = _gate(gate_ref="good_gate", status="green", days_clean=5, id=2)

        sb = mock_supabase_chain([])  # update returns empty
        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 11
        bot.send_message = AsyncMock(return_value=sent_mock)

        await _handle_breach(sb, _NOW, [breach_gate], [green_gate], bot=bot, musa_chat_id="123")

        sb.update.assert_called()
        update_arg = sb.update.call_args[0][0]
        assert update_arg["days_clean"] == 0

        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "breach" in text.lower() or "Pipeline" in text

    @pytest.mark.asyncio
    async def test_resets_without_bot_does_not_raise(self):
        breach_gate = _gate(gate_ref="bad_gate", status="breach", id=1)
        sb = mock_supabase_chain([])
        await _handle_breach(sb, _NOW, [breach_gate], [], bot=None, musa_chat_id=None)


# ---------------------------------------------------------------------------
# All 10 gates green test
# ---------------------------------------------------------------------------

class TestAllGatesGreen:

    @pytest.mark.asyncio
    async def test_run_tick_increments_all_green_gates(self):
        gates = [
            _gate(gate_ref=f"gate_{i}", status="green", days_clean=i, required=14, id=i+1)
            for i in range(10)
        ]

        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.update.return_value = sb
        sb.eq.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=gates),    # load gates
            *[MagicMock(data=[]) for _ in range(10)],  # 10 update calls
            MagicMock(data=[]),       # potential dedup/notification check
        ])

        with patch("nervous_system.pipeline_clock._notify_phase_complete",
                   new_callable=AsyncMock) as mock_notify:
            await _run_tick(sb, _NOW, bot=None, musa_chat_id=None)
            # update should be called 10 times
            assert sb.update.call_count == 10
