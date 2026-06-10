"""Telegram bot — inbound callback handler.

Per CAI-RESP-185 Q1: operator's [Approve][Defer][Delegate] button taps
+ free-text replies → operator decision written back into agent_messages.

Phase 1 implements the minimal contract:
  - Parse callback_data of the form '{action}:{agent_message_id}'
  - On [Approve]: write a new agent_messages row from operator → cai
    with message_type='agreed', body='approved via telegram',
    requires_response=False; mark original as responded_at.
  - On [Defer]: leave original UNCHANGED (requires_response stays True);
    write a brief audit-only agent_messages note from operator.
  - On [Delegate]: prompt operator for free-text reply via Telegram;
    on receipt, write that as the operator's response.
  - On any free-text reply: write as operator's decision back into
    agent_messages, referencing the original via thread_id.

Phase 1 deliberately keeps the bot single-handler and synchronous-feeling.
Full state machine for multi-turn delegation flows lives in Phase 2.
"""
from __future__ import annotations

import logging

import psycopg

from cc_cai_daemon.audit import AuditLogger

logger = logging.getLogger("cc_cai.telegram_bot")


def parse_callback_data(data: str) -> tuple[str, int] | None:
    """Parse '{action}:{msg_id}' format. Returns (action, msg_id) or None on malformed input."""
    if not isinstance(data, str) or ":" not in data:
        return None
    action, _, rest = data.partition(":")
    action = action.strip().lower()
    if action not in ("approve", "defer", "delegate"):
        return None
    try:
        msg_id = int(rest.strip())
    except ValueError:
        return None
    return action, msg_id


def handle_button_callback(
    dsn: str, audit: AuditLogger, action: str, msg_id: int,
) -> bool:
    """Apply an operator-button decision to agent_messages.

    Audit FIRST per INV-5; side effects only fire if audit row landed.
    Returns True on success, False on audit or DB failure.
    """
    audit_id = audit.log_tool_call(
        tool_name=f"operator_button_{action}",
        tool_input={"agent_message_id": msg_id, "action": action},
        agent_message_id=msg_id,
    )
    if audit_id is None:
        return False

    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            if action == "approve":
                # Fetch original to know thread_id + from_agent
                cur.execute(
                    "SELECT thread_id, from_agent, subject, is_test FROM agent_messages WHERE id = %s",
                    (msg_id,),
                )
                row = cur.fetchone()
                if not row:
                    logger.warning(f"approve: msg #{msg_id} not found")
                    return False
                thread_id, original_from, original_subject, source_is_test = row
                cur.execute(
                    "INSERT INTO agent_messages "
                    "(thread_id, from_agent, to_agent, message_type, subject, body, "
                    " requires_response, is_test, sub_tag, priority) "
                    "VALUES (%s, 'musa', 'cai', 'agreed', %s, %s, false, %s, "
                    "        'musa-button', 'P3')",
                    (thread_id, f"APPROVE: {original_subject[:80]}", "approved via telegram button",
                     source_is_test),
                )
                cur.execute(
                    "UPDATE agent_messages SET read_at = now(), responded_at = now() "
                    " WHERE id = %s",
                    (msg_id,),
                )
            elif action == "defer":
                # Don't touch read_at/responded_at; just log a deferred note in thread
                cur.execute(
                    "SELECT thread_id, subject, is_test FROM agent_messages WHERE id = %s",
                    (msg_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                thread_id, original_subject, source_is_test = row
                cur.execute(
                    "INSERT INTO agent_messages "
                    "(thread_id, from_agent, to_agent, message_type, subject, body, "
                    " requires_response, is_test, sub_tag, priority) "
                    "VALUES (%s, 'musa', 'cai', 'update', %s, %s, false, %s, "
                    "        'musa-button', 'P3')",
                    (thread_id, f"DEFER: {original_subject[:80]}", "deferred via telegram button",
                     source_is_test),
                )
            elif action == "delegate":
                # Phase 1: log the delegate intent; the multi-turn flow that
                # captures the free-text reply lives in handle_free_text_reply
                # below. Mark original as read so it doesn't keep alerting.
                cur.execute(
                    "SELECT thread_id, subject, is_test FROM agent_messages WHERE id = %s",
                    (msg_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                thread_id, original_subject, source_is_test = row
                cur.execute(
                    "INSERT INTO agent_messages "
                    "(thread_id, from_agent, to_agent, message_type, subject, body, "
                    " requires_response, is_test, sub_tag, priority) "
                    "VALUES (%s, 'musa', 'cai', 'update', %s, %s, false, %s, "
                    "        'musa-button', 'P3')",
                    (thread_id, f"DELEGATE: {original_subject[:80]}",
                     "delegated via telegram button — awaiting operator free-text reply",
                     source_is_test),
                )
                cur.execute(
                    "UPDATE agent_messages SET read_at = now() WHERE id = %s",
                    (msg_id,),
                )
        return True
    except Exception as e:
        logger.error(f"handle_button_callback failed for {action}:{msg_id}: {e}")
        return False


def handle_free_text_reply(
    dsn: str, audit: AuditLogger, thread_id: str, reply_text: str,
) -> bool:
    """Operator's free-text reply on a thread → posted as operator → cai message.

    Audit-first per INV-5. Returns True on success.
    """
    audit_id = audit.log_tool_call(
        tool_name="operator_free_text_reply",
        tool_input={"thread_id": thread_id, "reply_len": len(reply_text)},
    )
    if audit_id is None:
        return False
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages "
                "(thread_id, from_agent, to_agent, message_type, subject, body, "
                " requires_response, is_test, sub_tag, priority) "
                "VALUES (%s, 'musa', 'cai', 'update', %s, %s, false, false, "
                "        'musa-text', 'P3')",
                (thread_id, "operator-text-reply", reply_text[:8000]),
            )
        return True
    except Exception as e:
        logger.error(f"handle_free_text_reply failed: {e}")
        return False
