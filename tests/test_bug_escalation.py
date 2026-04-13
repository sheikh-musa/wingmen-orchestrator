"""Tests for bug escalation scheduler."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from nervous_system.bug_escalation import check_stale_bugs


def _make_bug(hours_ago: float, approvers=None):
    """Helper to create a mock bug dict with a created_at N hours ago."""
    created = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {
        "id": f"bug-{hours_ago}h",
        "repo_name": "test-repo",
        "reporter_name": "Test User",
        "description": "Something is broken",
        "confidence": "high",
        "approval_sent_to": approvers or [],
        "created_at": created,
    }


def _mock_supabase(bugs):
    """Build a supabase mock that returns the given bugs for chained query."""
    supabase = MagicMock()
    execute_mock = AsyncMock(return_value=MagicMock(data=bugs))
    supabase.table.return_value.select.return_value.eq.return_value.execute = execute_mock
    return supabase


@pytest.mark.asyncio
async def test_no_stale_bugs():
    """No bugs in proposed status -> no messages sent."""
    supabase = _mock_supabase([])
    bot = AsyncMock()

    await check_stale_bugs(supabase, bot)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_bug_no_notification():
    """Bug created 1 hour ago -> no notification yet."""
    supabase = _mock_supabase([_make_bug(1.0)])
    bot = AsyncMock()

    await check_stale_bugs(supabase, bot)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_four_hour_bug_sends_reminder():
    """Bug created 5 hours ago -> reminder sent to approvers."""
    supabase = _mock_supabase([_make_bug(5.0, approvers=["chat_123", "chat_456"])])
    bot = AsyncMock()

    await check_stale_bugs(supabase, bot)

    assert bot.send_message.call_count == 2
    chat_ids = [call.kwargs["chat_id"] for call in bot.send_message.call_args_list]
    assert "chat_123" in chat_ids
    assert "chat_456" in chat_ids


@pytest.mark.asyncio
async def test_twenty_four_hour_bug_escalates_to_super_admin():
    """Bug created 25 hours ago -> escalation to super admin."""
    supabase = _mock_supabase([_make_bug(25.0, approvers=["chat_123"])])
    bot = AsyncMock()

    with patch("nervous_system.bug_escalation.get_chat_id", return_value="musa_chat_id"):
        await check_stale_bugs(supabase, bot)

    # Should send to CTO group (or Musa fallback) only, not to approvers
    assert bot.send_message.call_count == 1
    call_kwargs = bot.send_message.call_args_list[0].kwargs
    assert call_kwargs["chat_id"] == "musa_chat_id"
    assert "24h+" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_no_approvers_no_crash():
    """Bug 5h old with no approvers -> no crash, no messages."""
    supabase = _mock_supabase([_make_bug(5.0, approvers=None)])
    bot = AsyncMock()

    await check_stale_bugs(supabase, bot)

    bot.send_message.assert_not_called()
