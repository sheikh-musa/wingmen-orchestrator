"""Tests for cc_cai_daemon.escalator — outbound Telegram push.

Per CAI-RESP-185 Q1: [Approve][Defer][Delegate] inline buttons + free-text
reply always available. Audit-first ordering per INV-5.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cc_cai_daemon.escalator import escalate_to_operator, _format_escalation_body


def _mk_msg(**kwargs) -> dict:
    base = {
        "id": 999,
        "from_agent": "cc-orchestrator",
        "to_agent": "cai",
        "message_type": "review_request",
        "priority": "P2",
        "subject": "test escalation subject",
        "body": "test body content",
        "requires_response": True,
    }
    base.update(kwargs)
    return base


async def test_escalate_calls_audit_first():
    order: list[str] = []
    audit = MagicMock()
    audit.log_escalation = MagicMock(side_effect=lambda **kw: (order.append("audit_esc"), 1)[1])
    audit.log_tool_call = MagicMock(side_effect=lambda **kw: (order.append("audit_tool"), 2)[1])

    bot = MagicMock()
    async def fake_send(*a, **kw):
        order.append("telegram")
        return MagicMock(message_id=12345)
    bot.send_message = fake_send

    result = await escalate_to_operator(
        bot, "chat-id", _mk_msg(), audit,
        reason="P2 needs operator", category="novel_low_confidence",
    )

    assert result == 12345
    assert order == ["audit_esc", "telegram", "audit_tool"]


async def test_audit_failure_aborts_escalation():
    audit = MagicMock()
    audit.log_escalation = MagicMock(return_value=None)  # audit failed
    audit.log_tool_call = MagicMock()

    bot = MagicMock()
    bot.send_message = AsyncMock()

    result = await escalate_to_operator(
        bot, "chat-id", _mk_msg(), audit,
        reason="x", category=None,
    )

    assert result is None
    bot.send_message.assert_not_called()
    audit.log_tool_call.assert_not_called()


async def test_telegram_failure_returns_none():
    audit = MagicMock()
    audit.log_escalation = MagicMock(return_value=1)
    audit.log_tool_call = MagicMock(return_value=2)

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))

    result = await escalate_to_operator(
        bot, "chat-id", _mk_msg(), audit,
        reason="x", category="novel_low_confidence",
    )

    assert result is None
    # First audit row landed; second (tool_call) did NOT (telegram failed)
    audit.log_escalation.assert_called_once()
    audit.log_tool_call.assert_not_called()


async def test_happy_path_returns_message_id():
    audit = MagicMock()
    audit.log_escalation = MagicMock(return_value=1)
    audit.log_tool_call = MagicMock(return_value=2)

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=99999))

    result = await escalate_to_operator(
        bot, "chat-id", _mk_msg(id=42), audit,
        reason="r", category="halal_riba",
    )

    assert result == 99999
    audit.log_escalation.assert_called_once_with(agent_message_id=42, reason="r")
    audit.log_tool_call.assert_called_once()


async def test_inline_keyboard_has_three_buttons_with_correct_callbacks():
    audit = MagicMock()
    audit.log_escalation = MagicMock(return_value=1)
    audit.log_tool_call = MagicMock(return_value=2)

    captured_markup = []
    bot = MagicMock()
    async def fake_send(*a, **kw):
        captured_markup.append(kw.get("reply_markup"))
        return MagicMock(message_id=1)
    bot.send_message = fake_send

    await escalate_to_operator(
        bot, "chat-id", _mk_msg(id=777), audit,
        reason="r", category="novel_low_confidence",
    )

    markup = captured_markup[0]
    # InlineKeyboardMarkup has inline_keyboard: list[list[Button]]
    keyboard = markup.inline_keyboard
    assert len(keyboard) == 1, "expected exactly 1 row"
    row = keyboard[0]
    assert len(row) == 3, "expected exactly 3 buttons"
    cbdatas = [b.callback_data for b in row]
    assert cbdatas[0] == "approve:777"
    assert cbdatas[1] == "defer:777"
    assert cbdatas[2] == "delegate:777"


async def test_body_includes_category_and_reason():
    audit = MagicMock()
    audit.log_escalation = MagicMock(return_value=1)
    audit.log_tool_call = MagicMock(return_value=2)

    captured_text = []
    bot = MagicMock()
    async def fake_send(*a, **kw):
        captured_text.append(kw.get("text"))
        return MagicMock(message_id=1)
    bot.send_message = fake_send

    await escalate_to_operator(
        bot, "chat-id", _mk_msg(), audit,
        reason="BNPL match", category="halal_riba",
    )

    text = captured_text[0]
    assert "halal_riba" in text
    assert "BNPL match" in text


async def test_body_truncates_long_subject_and_body():
    audit = MagicMock()
    audit.log_escalation = MagicMock(return_value=1)
    audit.log_tool_call = MagicMock(return_value=2)

    captured_text = []
    bot = MagicMock()
    async def fake_send(*a, **kw):
        captured_text.append(kw.get("text"))
        return MagicMock(message_id=1)
    bot.send_message = fake_send

    long_subject = "S" * 500
    long_body = "B" * 2000
    msg = _mk_msg(subject=long_subject, body=long_body)

    await escalate_to_operator(
        bot, "chat-id", msg, audit,
        reason="r", category="novel_low_confidence",
    )

    text = captured_text[0]
    # 120-char subject truncation
    assert "S" * 120 in text
    assert "S" * 121 not in text
    # 400-char body truncation
    assert "B" * 400 in text
    assert "B" * 401 not in text


async def test_category_none_renders_novel():
    audit = MagicMock()
    audit.log_escalation = MagicMock(return_value=1)
    audit.log_tool_call = MagicMock(return_value=2)

    captured_text = []
    bot = MagicMock()
    async def fake_send(*a, **kw):
        captured_text.append(kw.get("text"))
        return MagicMock(message_id=1)
    bot.send_message = fake_send

    await escalate_to_operator(
        bot, "chat-id", _mk_msg(), audit,
        reason="r", category=None,
    )

    text = captured_text[0]
    assert "novel" in text


def test_format_escalation_body_pure_function():
    """Smoke test the helper directly — no async, no mocks."""
    msg = _mk_msg(subject="hi", body="world")
    body = _format_escalation_body(msg, reason="r", category="zakat")
    assert "zakat" in body
    assert "hi" in body
    assert "world" in body
