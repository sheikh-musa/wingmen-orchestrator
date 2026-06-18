"""Outbound Telegram push for cc-cai escalations.

Per CAI-RESP-185 Q1: every escalation gets a Telegram message with inline
[Approve][Defer][Delegate] buttons + free-text reply always available.

Phase 1 implements the OUTBOUND side only — escalate_to_operator() pushes
the message and returns the telegram_message_id. The INBOUND side (button
callback handlers writing operator decisions back into agent_messages)
lives in telegram_bot.py and wires into the daemon main loop at Task 7.

Audit-first ordering per INV-5 hard ship condition:
  1. log_escalation row written before bot.send_message
  2. If audit fails → return None (escalation does NOT fire)
  3. If telegram fails → return None (first audit row stays as evidence
     of attempted escalation; second audit row NOT written)
  4. On success → return telegram_message_id, second audit row connects
     escalation to its concrete delivery
"""
from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from cc_cai_daemon.audit import AuditLogger

logger = logging.getLogger("cc_cai.escalator")


def _format_escalation_body(msg: dict, reason: str, category: str | None) -> str:
    """Format the Telegram body. Title / what / why / what-to-do per the ELI5
    convention (keep cc-cai escalator self-contained; do not depend on
    nervous_system/alert_format in Phase 1).
    """
    cat_label = category or "novel"
    subject = (msg.get("subject") or "")[:120]
    body_preview = (msg.get("body") or "")[:400]
    from_agent = msg.get("from_agent") or "?"
    priority = msg.get("priority") or "P?"
    mtype = msg.get("message_type") or "?"
    return (
        f"🚨 cc-cai escalation — {cat_label}\n\n"
        f"From: {from_agent} → cai\n"
        f"Subject: {subject}\n"
        f"Priority: {priority}\n"
        f"Type: {mtype}\n\n"
        f"Why escalated: {reason}\n\n"
        f"Body preview (first 400 chars):\n"
        f"{body_preview}\n\n"
        f"Tap [Approve] [Defer] [Delegate] below, or reply with free text."
    )


async def escalate_to_operator(
    bot,
    chat_id: str,
    msg: dict,
    audit: AuditLogger,
    reason: str,
    category: str | None,
) -> Optional[int]:
    """Push escalation to operator Telegram with inline button UI.

    Returns telegram message_id on success, None on either audit OR telegram
    failure. Audit always lands FIRST (INV-5 amanah precondition).
    """
    # 1. INV-5 audit first — escalation row before any side effect
    audit_id = audit.log_escalation(agent_message_id=msg["id"], reason=reason)
    if audit_id is None:
        logger.error(
            f"INV-5 audit failed for escalation of msg #{msg.get('id')} — "
            f"aborting Telegram push"
        )
        return None

    # 2. Build the body + inline keyboard
    text = _format_escalation_body(msg, reason, category)
    msg_id = msg["id"]
    # Approve/Defer/Delegate buttons removed — their handler (ctobot on the
    # retired @ihsanosbot) is gone; the operator replies via the @wingmennorchbot
    # bridge instead. Plain escalation text on the new bot.

    # 3. Send via Telegram
    try:
        sent = await bot.send_message(
            chat_id=chat_id, text=text,
        )
    except Exception as e:
        logger.error(
            f"Telegram send failed for escalation of msg #{msg_id}: {e}"
        )
        # First audit row stays (operator-auditable evidence of attempt).
        # Second audit row NOT written — no concrete delivery to log.
        return None

    # 4. Second audit row connects escalation to its concrete Telegram delivery
    audit.log_tool_call(
        tool_name="telegram_send_escalation",
        tool_input={"agent_message_id": msg_id, "category": category},
        tool_output={"telegram_message_id": sent.message_id},
        agent_message_id=msg_id,
    )

    return sent.message_id
