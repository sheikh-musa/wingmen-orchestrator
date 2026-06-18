"""Tests for agent_watchdog — ARCH-022 Layer 3."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from nervous_system.agent_watchdog import (
    check_agent_health,
    check_inbox_sla_violations,
    _check_heartbeat_staleness,
    _check_checkin_silence,
    _dedup_bucket,
)


_NOW = datetime.now(timezone.utc)
_STALE_HB = (_NOW - timedelta(minutes=45)).isoformat()   # 45min stale — triggers warn
_VERY_STALE_HB = (_NOW - timedelta(hours=3)).isoformat()  # 3h stale — triggers offline
_FRESH_HB = (_NOW - timedelta(minutes=5)).isoformat()    # recent — no alert


def _agent(id="cc-ihsanos", status="active", last_heartbeat=None):
    return {
        "id": id,
        "display_name": f"CC {id}",
        "status": status,
        "last_heartbeat": last_heartbeat or _STALE_HB,
    }


def _multi_execute_mock(*return_values):
    """Build a supabase mock that returns different values on sequential .execute() calls."""
    sb = MagicMock()
    sb.table.return_value = sb
    sb.select.return_value = sb
    sb.update.return_value = sb
    sb.eq.return_value = sb
    sb.gte.return_value = sb
    sb.lt.return_value = sb
    sb.like.return_value = sb
    sb.order.return_value = sb
    sb.limit.return_value = sb
    sb.insert.return_value = sb
    sb.execute = AsyncMock(side_effect=[MagicMock(data=d) for d in return_values])
    return sb


# ---------------------------------------------------------------------------
# Heartbeat staleness
# ---------------------------------------------------------------------------

class TestHeartbeatStaleness:

    @pytest.mark.asyncio
    async def test_no_stale_agents_does_nothing(self):
        sb = _multi_execute_mock([])  # no stale agents
        bot = AsyncMock()
        await _check_heartbeat_staleness(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_agent_sends_telegram(self):
        agent = _agent(last_heartbeat=_STALE_HB)
        sb = _multi_execute_mock(
            [agent],   # stale agents query
            [],        # dedup check (empty = not yet alerted)
            [],        # notification_log insert
        )
        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 42
        bot.send_message = AsyncMock(return_value=sent_mock)

        await _check_heartbeat_staleness(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "looks dead" in text.lower() or "stale" in text.lower() or "heartbeat" in text.lower()

    @pytest.mark.asyncio
    async def test_very_stale_agent_flipped_offline(self):
        agent = _agent(last_heartbeat=_VERY_STALE_HB)
        sb = _multi_execute_mock(
            [agent],  # stale agents query
            [],       # dedup check
            [],       # notification_log insert
        )
        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 1
        bot.send_message = AsyncMock(return_value=sent_mock)

        await _check_heartbeat_staleness(sb, bot=bot, musa_chat_id="123")

        # Should have called update to flip to offline
        sb.update.assert_called()
        update_calls = [str(c) for c in sb.update.call_args_list]
        assert any("offline" in c for c in update_calls)

    @pytest.mark.asyncio
    async def test_already_alerted_not_resent(self):
        agent = _agent(last_heartbeat=_STALE_HB)
        sb = _multi_execute_mock(
            [agent],           # stale agents query
            [{"id": 99}],      # dedup check — already exists
        )
        bot = AsyncMock()
        await _check_heartbeat_staleness(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_heartbeat_no_alert(self):
        # An agent with fresh heartbeat won't appear in the stale query
        sb = _multi_execute_mock([])  # query returns empty
        bot = AsyncMock()
        await _check_heartbeat_staleness(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Check-in silence
# ---------------------------------------------------------------------------

class TestCheckinSilence:

    @pytest.mark.asyncio
    async def test_no_active_cc_agents_does_nothing(self):
        sb = _multi_execute_mock([])  # no active cc-* agents
        bot = AsyncMock()
        await _check_checkin_silence(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_recent_message_no_alert(self):
        agent = _agent()
        recent_msg = [{"id": 1, "created_at": (_NOW - timedelta(minutes=10)).isoformat()}]
        sb = _multi_execute_mock(
            [agent],       # active cc-* agents
            recent_msg,    # recent message — within 45-min window
        )
        bot = AsyncMock()
        await _check_checkin_silence(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_old_message_sends_telegram(self):
        agent = _agent()
        old_msg = [{"id": 1, "created_at": (_NOW - timedelta(minutes=60)).isoformat()}]
        sb = _multi_execute_mock(
            [agent],   # active cc-* agents
            old_msg,   # last message was 60 min ago — over 45-min threshold
            [],        # dedup check (empty)
            [],        # notification_log insert
        )
        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 7
        bot.send_message = AsyncMock(return_value=sent_mock)

        await _check_checkin_silence(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "checked in" in text.lower() or "check" in text.lower()

    @pytest.mark.asyncio
    async def test_no_messages_ever_sends_telegram(self):
        agent = _agent()
        sb = _multi_execute_mock(
            [agent],   # active cc-* agents
            [],        # no messages at all
            [],        # dedup check empty
            [],        # notification_log insert
        )
        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 8
        bot.send_message = AsyncMock(return_value=sent_mock)

        await _check_checkin_silence(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_already_alerted_not_resent(self):
        agent = _agent()
        old_msg = [{"id": 1, "created_at": (_NOW - timedelta(minutes=60)).isoformat()}]
        sb = _multi_execute_mock(
            [agent],
            old_msg,
            [{"id": 55}],  # dedup check — already alerted
        )
        bot = AsyncMock()
        await _check_checkin_silence(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_checks_cc_prefix_agents(self):
        """Non-CC agents (cai, ralph) should not trigger check-in alerts."""
        # The .like("id", "cc-%") filter excludes non-CC agents.
        # Simulate: no cc-* agents active
        sb = _multi_execute_mock([])
        bot = AsyncMock()
        await _check_checkin_silence(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# check_agent_health (integration — both checks run)
# ---------------------------------------------------------------------------

class TestCheckAgentHealth:

    @pytest.mark.asyncio
    async def test_runs_heartbeat_only_checkin_and_sla_disabled(self):
        """Operator-disabled: check-in silence AND inbox-SLA P1 alarms are
        no-ops while autopilot is off (fake-autopilot nags). Heartbeat
        staleness still runs (process-liveness signal)."""
        from unittest.mock import patch
        sb = MagicMock()

        with patch("nervous_system.agent_watchdog._check_heartbeat_staleness",
                   new_callable=AsyncMock) as mock_hb, \
             patch("nervous_system.agent_watchdog._check_checkin_silence",
                   new_callable=AsyncMock) as mock_ci, \
             patch("nervous_system.agent_watchdog.check_inbox_sla_violations",
                   new_callable=AsyncMock) as mock_sla:
            await check_agent_health(sb)
            mock_hb.assert_called_once()
            mock_ci.assert_not_called()
            mock_sla.assert_not_called()


# ---------------------------------------------------------------------------
# _dedup_bucket
# ---------------------------------------------------------------------------

class TestDedupBucket:

    def test_same_hour_same_bucket(self):
        t1 = datetime(2026, 4, 16, 14, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 16, 14, 59, tzinfo=timezone.utc)
        assert _dedup_bucket(t1) == _dedup_bucket(t2)

    def test_different_hour_different_bucket(self):
        t1 = datetime(2026, 4, 16, 14, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 16, 15, 0, tzinfo=timezone.utc)
        assert _dedup_bucket(t1) != _dedup_bucket(t2)


# ---------------------------------------------------------------------------
# Inbox SLA P1 alarm — CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 4
# ---------------------------------------------------------------------------

def _violation(message_id=42, agent="cc-orchestrator", priority="P1",
               from_agent="cai", subject="P1 ruling",
               violation_type="unread", elapsed_minutes=120, threshold_minutes=60):
    return {
        "agent": agent, "message_id": message_id, "priority": priority,
        "from_agent": from_agent, "subject": subject,
        "violation_type": violation_type,
        "elapsed_minutes": elapsed_minutes,
        "threshold_minutes": threshold_minutes,
    }


class TestInboxSlaViolations:

    @pytest.mark.asyncio
    async def test_no_violations_no_calls(self):
        """Empty inbox_sla_violations → no Telegram, no notification_log writes."""
        sb = _multi_execute_mock([])  # view returns empty
        bot = AsyncMock()
        await check_inbox_sla_violations(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_p1_violation_alerts_when_not_dedup(self):
        """P1 violation + empty notification_log → Telegram + log row."""
        sb = _multi_execute_mock(
            [_violation()],   # view query
            [],               # _check_dedup → no existing row
            None,             # _send_and_log notification_log insert
        )
        bot = AsyncMock()
        sent = MagicMock(); sent.message_id = 99
        bot.send_message = AsyncMock(return_value=sent)
        await check_inbox_sla_violations(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_called_once()
        # Message body sanity
        sent_msg = bot.send_message.call_args.kwargs.get("text") or bot.send_message.call_args.args[1]
        assert "P1" in sent_msg
        assert "unread" in sent_msg
        assert "#42" in sent_msg

    @pytest.mark.asyncio
    async def test_dedup_skips_already_alerted(self):
        """Notification_log dedup row exists → no Telegram, no log."""
        sb = _multi_execute_mock(
            [_violation()],         # view query
            [{"id": "prev"}],       # _check_dedup → existing row
        )
        bot = AsyncMock()
        await check_inbox_sla_violations(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_unresponded_includes_respond_substantively_hint(self):
        """unresponded violations get the 'respond substantively' guidance."""
        v = _violation(violation_type="unresponded", elapsed_minutes=300, threshold_minutes=240)
        sb = _multi_execute_mock([v], [], None)
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        await check_inbox_sla_violations(sb, bot=bot, musa_chat_id="123")
        sent_msg = bot.send_message.call_args.kwargs.get("text") or ""
        assert "substantive" in sent_msg.lower() or "respond" in sent_msg.lower()

    @pytest.mark.asyncio
    async def test_failure_in_view_query_does_not_propagate(self):
        """View query exception → caught + logged; doesn't crash watchdog sweep."""
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.gte.return_value = sb
        sb.execute = AsyncMock(side_effect=RuntimeError("simulated DB outage"))
        # Must not raise
        await check_inbox_sla_violations(sb, bot=AsyncMock(), musa_chat_id="123")

    @pytest.mark.asyncio
    async def test_cutoff_filter_applied_to_query(self):
        """CAI-RESP-108: query MUST include .gte('created_at', CADENCE_001_FILING_DATE)
        so pre-CADENCE-001 transition-window violations are suppressed."""
        from nervous_system.agent_watchdog import CADENCE_001_FILING_DATE
        # Track which methods were called with which args
        calls = []
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        def _record_eq(*args, **kw):
            calls.append(("eq", args, kw)); return sb
        def _record_gte(*args, **kw):
            calls.append(("gte", args, kw)); return sb
        sb.eq.side_effect = _record_eq
        sb.gte.side_effect = _record_gte
        sb.execute = AsyncMock(return_value=MagicMock(data=[]))
        await check_inbox_sla_violations(sb, bot=None, musa_chat_id=None)
        gte_calls = [c for c in calls if c[0] == "gte"]
        assert any(args[0] == "created_at" and args[1] == CADENCE_001_FILING_DATE
                   for _, args, _ in gte_calls), \
            f"missing .gte('created_at', cutoff) — calls: {calls}"

    @pytest.mark.asyncio
    async def test_tombstone_suppresses_after_max_fires(self):
        """Per-message tombstone: after _SLA_ALARM_MAX_FIRES alarms have fired
        for the same (agent, msg_id, vtype) tuple, no further alarms."""
        from nervous_system.agent_watchdog import _SLA_ALARM_MAX_FIRES
        v = _violation(message_id=1038)
        # Mock supabase: view returns the violation, dedup check empty,
        # tombstone count returns >= max fires
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.gte.return_value = sb
        sb.like.return_value = sb
        sb.limit.return_value = sb
        # Sequential .execute() returns:
        #   (1) view query: 1 violation
        #   (2) dedup _check_dedup: empty (no current-hour fire)
        #   (3) tombstone count: count=_SLA_ALARM_MAX_FIRES
        tombstone_resp = MagicMock(data=[], count=_SLA_ALARM_MAX_FIRES)
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[v]),
            MagicMock(data=[]),
            tombstone_resp,
        ])
        bot = AsyncMock()
        await check_inbox_sla_violations(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_tombstone_allows_alert_when_under_threshold(self):
        """Below tombstone threshold → alarm fires normally."""
        from nervous_system.agent_watchdog import _SLA_ALARM_MAX_FIRES
        v = _violation(message_id=2000)
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.gte.return_value = sb
        sb.like.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        # Below threshold: count < _SLA_ALARM_MAX_FIRES
        tombstone_resp = MagicMock(data=[], count=_SLA_ALARM_MAX_FIRES - 1)
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[v]),
            MagicMock(data=[]),
            tombstone_resp,
            None,  # send_and_log notification_log insert
        ])
        bot = AsyncMock()
        sent = MagicMock(); sent.message_id = 99
        bot.send_message = AsyncMock(return_value=sent)
        await check_inbox_sla_violations(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_called_once()
