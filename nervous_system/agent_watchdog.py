"""
agent_watchdog.py — Heartbeat and check-in watchdog for active agents.

ARCH-022 Layer 3: catches sessions that bypass the launch_dangerous_cc.sh wrapper.

Two independent checks run every 20 polls (~10 min):

1. HEARTBEAT STALENESS
   - Query agents WHERE status='active' AND last_heartbeat < NOW() - 30min
   - Telegram Musa: "[agent-id] heartbeat stale — last seen X ago. Session may
     have died or CC is not following protocol."
   - At 2h stale: auto-flip agents.status='offline'

2. CHECK-IN SILENCE (ARCH-022 amendment)
   - Query agent_messages for the most recent outbound message from each active
     CC agent (from_agent LIKE 'cc-%')
   - If last message is > 45 min ago while agent is active: Telegram Musa
     "CC has not checked in for 45 minutes"
   - Heartbeat ≠ check-in: heartbeat proves the process is running,
     check-in proves the engineer is coordinating.

Deduplicates all alerts via notification_log.dedup_key, keyed on agent_id
and a 1-hour alert window so stale alerts do not spam.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from nervous_system import error_tracker

logger = logging.getLogger("wingmen.agent_watchdog")

_HEARTBEAT_STALE_WARN_MINUTES = 30
_HEARTBEAT_STALE_OFFLINE_HOURS = 2
_CHECKIN_SILENCE_MINUTES = 45
_ALERT_DEDUP_MINUTES = 60  # don't re-alert within this window


async def check_agent_health(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Run both heartbeat staleness and check-in silence checks.

    Called every 20 polls (~10 min) from the main orchestrator loop.
    """
    await _check_heartbeat_staleness(supabase, bot, musa_chat_id)
    await _check_checkin_silence(supabase, bot, musa_chat_id)


# ---------------------------------------------------------------------------
# Heartbeat staleness
# ---------------------------------------------------------------------------

async def _check_heartbeat_staleness(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Telegram alert for agents with stale heartbeats; flip offline at 2h."""
    try:
        now = datetime.now(timezone.utc)
        warn_cutoff = (now - timedelta(minutes=_HEARTBEAT_STALE_WARN_MINUTES)).isoformat()
        offline_cutoff = (now - timedelta(hours=_HEARTBEAT_STALE_OFFLINE_HOURS)).isoformat()

        result = await supabase.table("agents").select(
            "id, display_name, status, last_heartbeat"
        ).eq("status", "active").lt("last_heartbeat", warn_cutoff).execute()

        stale_agents = result.data or []
        if not stale_agents:
            return

        logger.info(f"agent_watchdog: {len(stale_agents)} agent(s) with stale heartbeat")

        for agent in stale_agents:
            agent_id: str = agent["id"]
            last_hb: str | None = agent.get("last_heartbeat")
            display_name: str = agent.get("display_name") or agent_id

            # Compute staleness for display
            stale_minutes: int | None = None
            if last_hb:
                try:
                    last_dt = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                    stale_minutes = int((now - last_dt).total_seconds() / 60)
                except ValueError:
                    pass

            # Flip to offline if 2h stale
            if last_hb and last_hb < offline_cutoff:
                logger.warning(
                    f"  {agent_id}: heartbeat > 2h stale — flipping to offline"
                )
                try:
                    await supabase.table("agents").update(
                        {"status": "offline"}
                    ).eq("id", agent_id).execute()
                except Exception as e:
                    logger.error(f"Failed to flip {agent_id} offline: {e}")
                    error_tracker.track_exception("agent_watchdog.flip_offline", e)

            # Dedup: don't re-alert within the alert window
            dedup_key = f"agent_watchdog:heartbeat_stale:{agent_id}:{_dedup_bucket(now)}"
            already = await _check_dedup(supabase, dedup_key)
            if already:
                continue

            stale_str = f"{stale_minutes} min" if stale_minutes else "unknown"
            msg = (
                f"\u26a0\ufe0f {display_name} heartbeat stale\n\n"
                f"Last seen: {stale_str} ago\n"
                f"Session may have died or CC is not following ARCH-022 protocol.\n\n"
                f"Check the session or run scripts/launch_dangerous_cc.sh to restart."
            )

            await _send_and_log(
                supabase,
                bot=bot,
                musa_chat_id=musa_chat_id,
                msg=msg,
                source="agent_watchdog.heartbeat_stale",
                decision_ref=agent_id,
                dedup_key=dedup_key,
            )

    except Exception as e:
        logger.error(f"agent_watchdog heartbeat check failed: {e}")
        error_tracker.track_exception("agent_watchdog.heartbeat_check", e)


# ---------------------------------------------------------------------------
# Check-in silence
# ---------------------------------------------------------------------------

async def _check_checkin_silence(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Telegram alert if an active CC agent has not posted a message in 45 min."""
    try:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=_CHECKIN_SILENCE_MINUTES)).isoformat()

        # Get all active CC agents
        result = await supabase.table("agents").select(
            "id, display_name, status"
        ).eq("status", "active").like("id", "cc-%").execute()

        active_cc = result.data or []
        if not active_cc:
            return

        for agent in active_cc:
            agent_id: str = agent["id"]
            display_name: str = agent.get("display_name") or agent_id

            # Find most recent outbound message from this agent
            msg_result = await supabase.table("agent_messages").select(
                "id, created_at"
            ).eq("from_agent", agent_id).order(
                "created_at", desc=True
            ).limit(1).execute()

            last_msgs = msg_result.data or []
            if last_msgs:
                last_created = last_msgs[0]["created_at"]
                if last_created >= cutoff:
                    continue  # Recently active — no alert
                try:
                    last_dt = datetime.fromisoformat(
                        last_created.replace("Z", "+00:00")
                    )
                    silence_minutes = int((now - last_dt).total_seconds() / 60)
                except ValueError:
                    silence_minutes = None
            else:
                # No messages at all — silence since session start
                silence_minutes = None

            dedup_key = f"agent_watchdog:checkin_silent:{agent_id}:{_dedup_bucket(now)}"
            already = await _check_dedup(supabase, dedup_key)
            if already:
                continue

            silence_str = f"{silence_minutes} min" if silence_minutes else "unknown duration"
            msg = (
                f"\U0001f4e1 {display_name} has not checked in\n\n"
                f"No agent_message in the last {silence_str}.\n"
                f"Agent status is active — heartbeat may be fine but coordination has stopped.\n\n"
                f"ARCH-022: post an update to cai every 30 min of active work."
            )

            await _send_and_log(
                supabase,
                bot=bot,
                musa_chat_id=musa_chat_id,
                msg=msg,
                source="agent_watchdog.checkin_silent",
                decision_ref=agent_id,
                dedup_key=dedup_key,
            )

    except Exception as e:
        logger.error(f"agent_watchdog check-in silence check failed: {e}")
        error_tracker.track_exception("agent_watchdog.checkin_check", e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedup_bucket(now: datetime) -> str:
    """Return an hour-granularity string for dedup key bucketing."""
    return now.strftime("%Y-%m-%dT%H")


async def _check_dedup(supabase, dedup_key: str) -> bool:
    """Return True if we already sent an alert with this dedup_key."""
    try:
        existing = (
            await supabase.table("notification_log")
            .select("id")
            .eq("dedup_key", dedup_key)
            .limit(1)
            .execute()
        )
        return bool(existing.data)
    except Exception as e:
        logger.warning(f"Dedup check failed for {dedup_key}: {e}")
        error_tracker.track_exception("agent_watchdog.dedup_check", e)
        return False  # Fail open: attempt notification


async def _send_and_log(
    supabase,
    *,
    bot,
    musa_chat_id: str | None,
    msg: str,
    source: str,
    decision_ref: str,
    dedup_key: str,
) -> None:
    """Send Telegram + log to notification_log."""
    telegram_msg_id: int | None = None

    if bot and musa_chat_id:
        try:
            sent = await bot.send_message(chat_id=musa_chat_id, text=msg)
            telegram_msg_id = sent.message_id
            logger.info(f"  Watchdog alert sent: {dedup_key}")
        except Exception as e:
            logger.error(f"Watchdog Telegram send failed: {e}")
            error_tracker.track_exception("agent_watchdog.telegram_send", e)
            return  # Don't log dedup entry if send failed
    else:
        logger.info(f"  Watchdog alert (no bot): {dedup_key}")

    try:
        await supabase.table("notification_log").insert(
            {
                "source": source,
                "decision_ref": decision_ref[:100],
                "channel": "telegram",
                "recipient": musa_chat_id or "unknown",
                "message_text": msg,
                "telegram_msg_id": telegram_msg_id,
                "dedup_key": dedup_key,
            }
        ).execute()
    except Exception as e:
        logger.error(f"Watchdog notification_log insert failed: {e}")
        error_tracker.track_exception("agent_watchdog.log_alert", e)
