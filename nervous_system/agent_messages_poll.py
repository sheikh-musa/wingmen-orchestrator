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

After notifying, messages are stamped forwarded_to_telegram_at=NOW()
and logged to notification_log with a dedup_key to prevent double-sends
on restart. BUG-021: the addressed agent's own read_at column is never
touched by the notifier — that's reserved for the agent's processing
stamp, so agents can still detect unhandled mail after forwarding.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from nervous_system import error_tracker

logger = logging.getLogger("wingmen.agent_messages_poll")

# Agents whose inbound traffic routes to Telegram
# (Musa monitors cai's inbox; "musa" = direct messages to Musa)
TELEGRAM_ROUTED_TARGETS = {"cai", "musa", "broadcast"}

# CC agent prefix — both-sided prefix = internal peer traffic, no Telegram
_CC_PREFIX = "cc-"

# ARCH-035: subjects on agent_messages starting with any of these prefixes
# belong in agent_status, not agent_messages. The poller drops them so the
# row stays UNREAD as a tripwire for the sending agent. Nightly pg_cron purges
# after 24h — see supabase/migrations/20260420_governance_hygiene_batch.sql
# Section D.1. The regex below MUST stay in sync with the cron DELETE's
# `subject ~` pattern — if you change one, change the other. Follow-up to
# extract into a shared source: GOVERNANCE-CLEANUP-001 H6.
_BANNED_PREFIX_RE = re.compile(r'^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):')


def _is_routable(r: dict) -> bool:
    """Return True if this row should reach Musa's Telegram.

    Filters:
    1. Banned-prefix rows dropped (ARCH-035) — left UNREAD as tripwire.
    2. CC-to-CC peer traffic dropped.
    3. cai → cc-* relayed.
    4. Everything else: only TELEGRAM_ROUTED_TARGETS addressees.
    """
    subject = r.get("subject") or ""
    if _BANNED_PREFIX_RE.match(subject):
        logger.info(
            f"poll: dropping banned-prefix msg id={r.get('id')} "
            f"subject={subject[:60]}"
        )
        return False

    from_a = r.get("from_agent", "")
    to_a = r.get("to_agent")
    if _is_cc_to_cc(from_a, to_a):
        return False
    if from_a == "cai" and bool(to_a) and to_a.startswith(_CC_PREFIX):
        return True
    return to_a in TELEGRAM_ROUTED_TARGETS


def _is_cc_to_cc(from_agent: str, to_agent: str | None) -> bool:
    """Return True if both sides are CC agents (internal peer traffic)."""
    return (
        from_agent.startswith(_CC_PREFIX)
        and bool(to_agent)
        and to_agent.startswith(_CC_PREFIX)
    )


# ARCH-036: priority glyph prefix + P3 suppression.
# P0/P1/P2 prepend a colored circle. P3 is suppressed from Telegram entirely
# (Telegram is interrupt-capable, P3 is passive FYI). See CAI-RESP-047 for why
# P3 suppression is load-bearing alongside the ARCH-035 banned-prefix filter.
_PRIORITY_GLYPH = {"P0": "\U0001f534", "P1": "\U0001f7e0", "P2": "\U0001f7e1"}


def _format_telegram(msg: dict) -> str | None:
    """Format an agent_messages row into a Telegram string with priority glyph.

    Returns None if this message should not be routed to Telegram (P3, or the
    body formatter filtered it out e.g. CC-to-CC peer traffic).
    """
    priority = msg.get("priority") or "P2"
    if priority == "P3":
        return None
    base = _format_telegram_body(msg)
    if base is None:
        return None
    glyph = _PRIORITY_GLYPH.get(priority, _PRIORITY_GLYPH["P2"])
    return f"{glyph} {base}"


def _format_telegram_body(msg: dict) -> str | None:
    """Format an agent_messages row into a Telegram string (no priority glyph).

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

    # cai → cc-* messages: short trigger — both agents read Supabase directly
    cai_to_cc = (
        from_agent == "cai"
        and bool(to_agent)
        and to_agent.startswith(_CC_PREFIX)
    )
    if cai_to_cc:
        flag = "🔔" if requires_response else "📨"
        return (
            f"{flag} cai → {to_agent}\n"
            f"{subject}\n\n"
            f"Tell {to_agent} to check agent_messages."
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
        # Short trigger — cai reads Supabase directly, no body paste needed
        return (
            f"\u2694\ufe0f {from_agent} → {to_agent}\n"
            f"{subject}\n\n"
            f"Tell {to_agent} to check agent_messages."
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
        # ARCH-036: sort priority-first so urgent traffic drains before backlog.
        result = await supabase.table("agent_messages").select(
            "id, from_agent, to_agent, message_type, subject, body, "
            "requires_response, priority, created_at"
        ).is_("read_at", "null").is_(
            "forwarded_to_telegram_at", "null"
        ).is_("skipped_at", "null").eq("is_test", False).order("priority", desc=False).order(
            "requires_response", desc=True
        ).order("created_at", desc=False).execute()

        rows: list[dict] = result.data or []
        if not rows:
            return

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
                # P3 suppression or non-routable shape — stamp skipped_at
                # (GOVERNANCE-CLEANUP-001 Step 2) so the poll hot-set doesn't
                # loop over it every 5-min cycle.
                await _mark_skipped(supabase, msg_id)
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

            # BUG-021: stamp forwarded_to_telegram_at (notifier's column),
            # never read_at. read_at is reserved for the addressed agent's
            # own processing stamp — cc-* agents set it when they handle
            # the message. Keeping semantics honest lets agents detect
            # unprocessed mail via read_at IS NULL regardless of forwarding.
            await _mark_forwarded(supabase, msg_id)

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
            # Ensure forwarded_to_telegram_at is set even if a previous run
            # notified but didn't stamp it (crash between log + stamp)
            await _mark_forwarded(supabase, msg_id)
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


async def _mark_forwarded(supabase, msg_id: int) -> None:
    """Set forwarded_to_telegram_at on an agent_messages row.

    BUG-021: middleware must NEVER write read_at — that column is reserved
    for the addressed agent's own processing stamp. Forwarding state lives
    on its own column so semantics stay honest.
    """
    try:
        await supabase.table("agent_messages").update(
            {"forwarded_to_telegram_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", msg_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark agent_message {msg_id} as forwarded: {e}")
        error_tracker.track_exception("agent_messages_poll.mark_forwarded", e)


async def _mark_skipped(supabase, msg_id: int) -> None:
    """Stamp agent_messages.skipped_at=now() on a row the notifier decided
    not to route. Mutually exclusive with forwarded_to_telegram_at by
    convention — a row is either forwarded or skipped, never both.
    GOVERNANCE-CLEANUP-001 Step 2.
    """
    try:
        await supabase.table("agent_messages").update(
            {"skipped_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", msg_id).execute()
    except Exception as e:
        logger.warning(f"Failed to stamp skipped_at for message {msg_id}: {e}")
        error_tracker.track_exception("agent_messages_poll.mark_skipped", e)
