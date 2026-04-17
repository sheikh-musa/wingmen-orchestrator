"""
agent_messages_poll.py — Poll agent_messages and route to Telegram.

TASK-041: Wire agent_messages into orchestrator Telegram notifications.

Polls agent_messages for unread messages and routes them to Musa's
Telegram based on message_type and routing rules:

  - requires_response=True  → urgent "CC needs your input" format
  - message_type=blocker    → 🚨 BLOCKER with first 200 chars of body
  - message_type=question   → ❓ question format
  - message_type=review_request → 👀 review request
  - message_type=decision   → 📋 decision format
  - message_type=update     → 📦 brief subject line only
  - CC-to-CC traffic        → skipped entirely (internal peer traffic)

After notifying, messages are marked read_at=NOW() and logged to
notification_log with a dedup_key to prevent double-sends on restart.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from nervous_system import error_tracker

logger = logging.getLogger("wingmen.agent_messages_poll")

# Agents whose inbound traffic routes to Telegram
# (Musa monitors cai's inbox; "musa" = direct messages to Musa)
TELEGRAM_ROUTED_TARGETS = {"cai", "musa", "broadcast"}

# CC agent prefix — both-sided prefix = internal peer traffic, no Telegram
_CC_PREFIX = "cc-"


def _is_cc_to_cc(from_agent: str, to_agent: str | None) -> bool:
    """Return True if both sides are CC agents (internal peer traffic)."""
    return (
        from_agent.startswith(_CC_PREFIX)
        and bool(to_agent)
        and to_agent.startswith(_CC_PREFIX)
    )


def _format_telegram(msg: dict) -> str | None:
    """Format an agent_messages row into a Telegram string.

    Returns None if this message should not be routed to Telegram.
    """
    from_agent: str = msg.get("from_agent", "?")
    to_agent: str | None = msg.get("to_agent")
    message_type: str = msg.get("message_type") or "update"
    subject: str = msg.get("subject") or "(no subject)"
    body: str = msg.get("body") or ""
    requires_response: bool = bool(msg.get("requires_response"))

    # Skip CC-to-CC messages — internal peer traffic
    if _is_cc_to_cc(from_agent, to_agent):
        return None

    # cai → cc-* messages: route to Musa as relay — he pastes into active session
    cai_to_cc = (
        from_agent == "cai"
        and bool(to_agent)
        and to_agent.startswith(_CC_PREFIX)
    )
    if cai_to_cc:
        snippet = (body[:600] + "\n...") if len(body) > 600 else body
        urgency = "🔔 cai → relay to active session" if requires_response else "📨 cai message"
        return (
            f"{urgency}\n\n"
            f"To: {to_agent}\n"
            f"{subject}\n\n"
            f"{snippet}\n\n"
            f"Paste this into the active {to_agent} session."
        )

    # Only route to known Telegram targets
    if to_agent not in TELEGRAM_ROUTED_TARGETS:
        return None

    if requires_response:
        return (
            f"\U0001f514 CC needs your input\n\n"
            f"From: {from_agent}\n"
            f"{subject}\n\n"
            f"Reply to this message to respond as cai."
        )

    if message_type == "challenge":
        snippet = (body[:400] + "\n...") if len(body) > 400 else body
        return (
            f"\u2694\ufe0f CC challenge — take to cai\n\n"
            f"{subject}\n\n"
            f"{snippet}\n\n"
            f"Reply to this message with cai\u2019s response."
        )

    if message_type == "agreed":
        return f"\u2705 CC agreed\n\n{subject}"

    if message_type == "blocker":
        snippet = (body[:200] + "...") if len(body) > 200 else body
        return f"\U0001f6a8 BLOCKER from {from_agent}\n\n{subject}\n\n{snippet}"

    if message_type == "question":
        return f"\u2753 CC question from {from_agent}\n\n{subject}"

    if message_type == "review_request":
        return f"\U0001f440 Review requested by {from_agent}\n\n{subject}"

    if message_type == "decision":
        return f"\U0001f4cb Decision from {from_agent}\n\n{subject}"

    # Default: update (most common — CC-UPDATE-NNN rows)
    return f"\U0001f4e6 {subject}"


async def poll_agent_messages(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Poll agent_messages for unread messages and route to Telegram.

    Called every 10 polls (~5 min) from the main orchestrator loop.
    Skips CC-to-CC peer traffic. Marks routed messages as read.
    Deduplicates via notification_log.dedup_key so restarts are safe.
    """
    try:
        result = await supabase.table("agent_messages").select(
            "id, from_agent, to_agent, message_type, subject, body, "
            "requires_response, created_at"
        ).is_("read_at", "null").order("created_at", desc=False).execute()

        rows: list[dict] = result.data or []
        if not rows:
            return

        def _is_routable(r: dict) -> bool:
            from_a = r.get("from_agent", "")
            to_a = r.get("to_agent")
            if _is_cc_to_cc(from_a, to_a):
                return False
            # cai messages addressed to any cc-* agent relay through Musa
            if from_a == "cai" and bool(to_a) and to_a.startswith(_CC_PREFIX):
                return True
            return to_a in TELEGRAM_ROUTED_TARGETS

        routable = [r for r in rows if _is_routable(r)]

        if not routable:
            logger.debug(
                f"agent_messages_poll: {len(rows)} unread, none routable to Telegram"
            )
            return

        logger.info(
            f"agent_messages_poll: {len(rows)} unread, "
            f"{len(routable)} routable to Telegram"
        )

        for msg in routable:
            msg_id: int = msg["id"]
            dedup_key = f"agent_message:{msg_id}:telegram"

            # Dedup: skip if we already notified for this message
            already_notified = await _already_notified(supabase, dedup_key, msg_id)
            if already_notified:
                continue

            text = _format_telegram(msg)
            if text is None:
                # Should not happen here (pre-filtered above), but guard anyway
                continue

            sent_telegram_id: int | None = None
            if bot and musa_chat_id:
                try:
                    sent = await bot.send_message(chat_id=musa_chat_id, text=text)
                    sent_telegram_id = sent.message_id
                    logger.info(
                        f"  -> message {msg_id} routed to Telegram "
                        f"(type={msg.get('message_type')}, "
                        f"requires_response={msg.get('requires_response')})"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send Telegram for agent_message {msg_id}: {e}"
                    )
                    error_tracker.track_exception(
                        "agent_messages_poll.telegram_send", e
                    )
                    continue  # Don't mark read if Telegram failed
            else:
                logger.info(
                    f"  -> message {msg_id} would route to Telegram "
                    f"(no bot configured)"
                )

            # Log to notification_log for dedup + observability
            await _log_notification(
                supabase,
                msg_id=msg_id,
                subject=msg.get("subject", "")[:100],
                text=text,
                musa_chat_id=musa_chat_id,
                telegram_msg_id=sent_telegram_id,
                dedup_key=dedup_key,
            )

            # Mark the message as read — prevents re-notification on next poll
            await _mark_read(supabase, msg_id)

    except Exception as e:
        logger.error(f"agent_messages_poll failed: {e}")
        error_tracker.track_exception("agent_messages_poll.main", e)


async def _already_notified(supabase, dedup_key: str, msg_id: int) -> bool:
    """Check notification_log for a prior send; also mark read if found."""
    try:
        existing = (
            await supabase.table("notification_log")
            .select("id")
            .eq("dedup_key", dedup_key)
            .limit(1)
            .execute()
        )
        if existing.data:
            logger.debug(
                f"Skipping agent_message {msg_id} — already notified "
                f"(dedup_key={dedup_key})"
            )
            # Ensure read_at is set even if a previous run notified but didn't mark read
            await _mark_read(supabase, msg_id)
            return True
        return False
    except Exception as e:
        logger.warning(f"Dedup check failed for message {msg_id}: {e}")
        error_tracker.track_exception("agent_messages_poll.dedup_check", e)
        return False  # Fail open: attempt notification rather than miss it


async def _log_notification(
    supabase,
    *,
    msg_id: int,
    subject: str,
    text: str,
    musa_chat_id: str | None,
    telegram_msg_id: int | None,
    dedup_key: str,
) -> None:
    """Persist notification to notification_log for observability + dedup."""
    try:
        await supabase.table("notification_log").insert(
            {
                "source": "agent_messages_poll",
                "decision_ref": subject,
                "channel": "telegram",
                "recipient": musa_chat_id or "unknown",
                "message_text": text,
                "telegram_msg_id": telegram_msg_id,
                "dedup_key": dedup_key,
            }
        ).execute()
    except Exception as e:
        logger.error(
            f"Failed to log notification for agent_message {msg_id}: {e}"
        )
        error_tracker.track_exception(
            "agent_messages_poll.log_notification", e
        )


async def _mark_read(supabase, msg_id: int) -> None:
    """Set read_at on an agent_messages row."""
    try:
        await supabase.table("agent_messages").update(
            {"read_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", msg_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark agent_message {msg_id} as read: {e}")
        error_tracker.track_exception("agent_messages_poll.mark_read", e)
