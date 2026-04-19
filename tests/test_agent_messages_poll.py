"""Tests for agent_messages_poll — TASK-041."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nervous_system.agent_messages_poll import (
    poll_agent_messages,
    _format_telegram,
    _is_cc_to_cc,
    _is_routable,
)
from tests.conftest import mock_supabase_chain


# ---------------------------------------------------------------------------
# Unit tests — _is_cc_to_cc
# ---------------------------------------------------------------------------

class TestIsCcToCc:
    def test_both_cc_agents(self):
        assert _is_cc_to_cc("cc-ihsanos", "cc-web") is True

    def test_from_cc_to_cai(self):
        assert _is_cc_to_cc("cc-ihsanos", "cai") is False

    def test_from_cc_to_musa(self):
        assert _is_cc_to_cc("cc-ihsanos", "musa") is False

    def test_from_non_cc(self):
        assert _is_cc_to_cc("ralph", "cai") is False

    def test_to_agent_none(self):
        assert _is_cc_to_cc("cc-ihsanos", None) is False


# ---------------------------------------------------------------------------
# Unit tests — _format_telegram
# ---------------------------------------------------------------------------

class TestFormatTelegram:
    def _msg(self, **kwargs):
        return {
            "from_agent": "cc-ihsanos",
            "to_agent": "cai",
            "message_type": "update",
            "subject": "CC-UPDATE-030: test subject",
            "body": "Some body text.",
            "requires_response": False,
            **kwargs,
        }

    def test_update_message_returns_subject(self):
        text = _format_telegram(self._msg())
        assert text is not None
        assert "CC-UPDATE-030: test subject" in text

    def test_requires_response_urgent_format(self):
        text = _format_telegram(self._msg(requires_response=True))
        assert text is not None
        assert "CC needs your input" in text
        assert "cc-ihsanos" in text
        assert "claude.ai" in text

    def test_blocker_includes_body_snippet(self):
        text = _format_telegram(self._msg(message_type="blocker", body="x" * 300))
        assert text is not None
        assert "BLOCKER" in text
        assert "..." in text  # truncated

    def test_blocker_short_body_no_ellipsis(self):
        text = _format_telegram(self._msg(message_type="blocker", body="short body"))
        assert text is not None
        assert "short body" in text
        assert "..." not in text

    def test_question_format(self):
        text = _format_telegram(self._msg(message_type="question"))
        assert text is not None
        assert "question" in text.lower()

    def test_review_request_format(self):
        text = _format_telegram(self._msg(message_type="review_request"))
        assert text is not None
        assert "Review" in text

    def test_decision_format(self):
        text = _format_telegram(self._msg(message_type="decision"))
        assert text is not None
        assert "Decision" in text

    def test_cc_to_cc_returns_none(self):
        text = _format_telegram(self._msg(to_agent="cc-web"))
        assert text is None

    def test_unknown_to_agent_returns_none(self):
        text = _format_telegram(self._msg(to_agent="some-other-agent"))
        assert text is None

    def test_musa_to_agent_routes(self):
        text = _format_telegram(self._msg(to_agent="musa"))
        assert text is not None

    def test_broadcast_to_agent_routes(self):
        text = _format_telegram(self._msg(to_agent="broadcast"))
        assert text is not None


# ---------------------------------------------------------------------------
# Integration tests — poll_agent_messages
# ---------------------------------------------------------------------------

def _make_msg(
    id: int,
    from_agent: str = "cc-ihsanos",
    to_agent: str = "cai",
    message_type: str = "update",
    subject: str = "CC-UPDATE-030: done",
    body: str = "body text",
    requires_response: bool = False,
) -> dict:
    return {
        "id": id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "message_type": message_type,
        "subject": subject,
        "body": body,
        "requires_response": requires_response,
        "created_at": "2026-04-16T00:00:00Z",
    }


class TestPollAgentMessages:

    @pytest.mark.asyncio
    async def test_empty_inbox_returns_early(self):
        sb = mock_supabase_chain([])
        bot = AsyncMock()
        await poll_agent_messages(sb, bot=bot, musa_chat_id="123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_message_sends_telegram(self):
        msg = _make_msg(1, message_type="update", subject="CC-UPDATE-030: step done")
        # First execute() = agent_messages rows
        # Second execute() = notification_log dedup check (empty = not yet notified)
        # Third execute() = notification_log insert
        # Fourth execute() = agent_messages update (mark read)
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb

        # Sequence: rows → dedup empty → log insert → mark read
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),       # agent_messages select
            MagicMock(data=[]),          # notification_log dedup check (empty)
            MagicMock(data=[]),          # notification_log insert
            MagicMock(data=[]),          # agent_messages update (mark read)
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 42
        bot.send_message = AsyncMock(return_value=sent_mock)

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")
        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert "CC-UPDATE-030: step done" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_requires_response_sends_urgent_format(self):
        msg = _make_msg(2, requires_response=True, subject="Need input on X")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),
            MagicMock(data=[]),  # dedup empty
            MagicMock(data=[]),  # log insert
            MagicMock(data=[]),  # mark read
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 99
        bot.send_message = AsyncMock(return_value=sent_mock)

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "CC needs your input" in text
        assert "claude.ai" in text

    @pytest.mark.asyncio
    async def test_blocker_sends_urgent_format(self):
        msg = _make_msg(3, message_type="blocker", subject="DB migration failed", body="Details here")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),
            MagicMock(data=[]),
            MagicMock(data=[]),
            MagicMock(data=[]),
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 77
        bot.send_message = AsyncMock(return_value=sent_mock)

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "BLOCKER" in text

    @pytest.mark.asyncio
    async def test_cc_to_cc_message_not_sent(self):
        """CC-to-CC traffic must never reach Telegram."""
        msg = _make_msg(4, from_agent="cc-ihsanos", to_agent="cc-web")
        sb = mock_supabase_chain([msg])
        bot = AsyncMock()
        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_notified_not_sent_again(self):
        """Messages with existing notification_log entry are skipped."""
        msg = _make_msg(5, subject="CC-UPDATE-025: already sent")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),               # agent_messages select
            MagicMock(data=[{"id": 99}]),         # dedup check — already exists!
            MagicMock(data=[]),                   # mark read (cleanup)
        ])

        bot = AsyncMock()
        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_telegram_failure_does_not_mark_forwarded(self):
        """If Telegram send fails, forwarded_to_telegram_at stays NULL for retry."""
        msg = _make_msg(6, subject="CC-UPDATE-031: failed send")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),    # agent_messages select
            MagicMock(data=[]),       # dedup empty
        ])

        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=Exception("Telegram timeout"))

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")

        # Mark read should NOT have been called (send failed → continue)
        update_call_count = sum(
            1 for call in sb.method_calls
            if "update" in str(call)
        )
        assert update_call_count == 0

    @pytest.mark.asyncio
    async def test_works_for_any_cc_agent(self):
        """Must work for any from_agent, not just cc-ihsanos."""
        msg = _make_msg(7, from_agent="cc-scholar", subject="CC-UPDATE-999: from scholar")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),
            MagicMock(data=[]),  # dedup
            MagicMock(data=[]),  # log
            MagicMock(data=[]),  # mark read
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 11
        bot.send_message = AsyncMock(return_value=sent_mock)

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        # Update format includes subject; cc-scholar is shown for non-update types.
        # What matters is the message was routed at all.
        assert "CC-UPDATE-999" in text

    @pytest.mark.asyncio
    async def test_no_bot_configured_does_not_raise(self):
        """When bot is None (test environment), poll should not raise."""
        msg = _make_msg(8, subject="Update without bot")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),
            MagicMock(data=[]),  # dedup
            MagicMock(data=[]),  # log
            MagicMock(data=[]),  # mark read
        ])

        # Should complete without error even with no bot
        await poll_agent_messages(sb, bot=None, musa_chat_id=None)

    @pytest.mark.asyncio
    async def test_successful_forward_stamps_forwarded_to_telegram_at(self):
        """After a successful Telegram send, forwarded_to_telegram_at is set."""
        msg = _make_msg(100, to_agent="musa", subject="BUG-021: positive case")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),       # agent_messages select
            MagicMock(data=[]),          # dedup empty
            MagicMock(data=[]),          # log insert
            MagicMock(data=[]),          # mark forwarded
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 100
        bot.send_message = AsyncMock(return_value=sent_mock)

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")

        update_calls = list(sb.update.call_args_list)
        assert any(
            "forwarded_to_telegram_at" in str(call)
            for call in update_calls
        ), f"Expected update with forwarded_to_telegram_at, got: {update_calls}"

    @pytest.mark.asyncio
    async def test_forwarding_does_not_modify_read_at(self):
        """BUG-021 regression guard: middleware must never write read_at."""
        msg = _make_msg(101, to_agent="musa", subject="BUG-021: regression guard")
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.is_.return_value = sb
        sb.order.return_value = sb
        sb.eq.return_value = sb
        sb.limit.return_value = sb
        sb.insert.return_value = sb
        sb.update.return_value = sb
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[msg]),
            MagicMock(data=[]),
            MagicMock(data=[]),
            MagicMock(data=[]),
        ])

        bot = AsyncMock()
        sent_mock = MagicMock()
        sent_mock.message_id = 101
        bot.send_message = AsyncMock(return_value=sent_mock)

        await poll_agent_messages(sb, bot=bot, musa_chat_id="123456")

        for call in sb.update.call_args_list:
            args, _kwargs = call
            if args:
                payload = args[0]
                assert "read_at" not in payload, (
                    f"BUG-021 regression: middleware wrote read_at: {payload}"
                )


# ARCH-035: banned-prefix rejection in _is_routable
class TestBannedPrefixRejection:
    """Per ARCH-035: CLAIM/STATUS/HEARTBEAT/DIGEST/COMPLETE prefixes on
    agent_messages subjects belong in agent_status, not agent_messages.
    The poller drops them (no Telegram, no forwarded_to_telegram_at stamp).
    The row stays UNREAD as a tripwire so the sending agent can be coached."""

    def _row(self, subject: str, from_agent: str = "cc-ihsanos", to_agent: str = "cai"):
        return {
            "id": 1,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "message_type": "update",
            "subject": subject,
            "body": "x",
            "requires_response": False,
        }

    def test_claim_prefix_rejected(self):
        assert _is_routable(self._row("CLAIM: working on BUG-042")) is False

    def test_status_prefix_rejected(self):
        assert _is_routable(self._row("STATUS: idle")) is False

    def test_heartbeat_prefix_rejected(self):
        assert _is_routable(self._row("HEARTBEAT: 2026-04-19T12:00Z")) is False

    def test_digest_prefix_rejected(self):
        assert _is_routable(self._row("DIGEST: session-end summary")) is False

    def test_complete_prefix_rejected(self):
        assert _is_routable(self._row("COMPLETE: TASK-042 shipped")) is False

    def test_normal_subject_still_routed(self):
        assert _is_routable(self._row("BUG-042: trigger rewrite for review")) is True

    def test_lowercase_prefix_not_rejected(self):
        # Intentional — only uppercase convention is banned.
        assert _is_routable(self._row("claim: normal message")) is True

    def test_prefix_mid_subject_not_rejected(self):
        # Only leading prefix is banned.
        assert _is_routable(self._row("re: CLAIM: normal message")) is True

    def test_missing_subject_not_rejected(self):
        # Row with null subject — don't crash on .match(None)
        r = self._row("")
        r["subject"] = None
        # Row has to_agent='cai' so it IS normally routable — banned-prefix
        # check should not false-positive on None.
        assert _is_routable(r) is True
