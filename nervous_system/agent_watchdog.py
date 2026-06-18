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
from nervous_system.alert_format import format_alert

logger = logging.getLogger("wingmen.agent_watchdog")

_HEARTBEAT_STALE_WARN_MINUTES = 30
_HEARTBEAT_STALE_OFFLINE_HOURS = 2
_CHECKIN_SILENCE_MINUTES = 45
_ALERT_DEDUP_MINUTES = 60  # don't re-alert within this window

# CAI-PROCESS-INBOX-CADENCE-001 filing timestamp. Per CAI-RESP-108: P1
# alarms suppressed for messages created before this cutoff (transition-
# window noise — pre-Section A backlog with mis-set requires_response=true,
# plus cai's pre-discipline unread inbox). "No retroactive cleanup" applies
# to metadata; this constant gates the alarm path so the queue drains
# mechanically without spamming Musa with 30+ Telegrams/hour.
# Bumpable via future cai amendment.
CADENCE_001_FILING_DATE = "2026-04-28T22:30:00+00:00"

# Per-message tombstone threshold. After N hour-bucket alarms have fired
# for the same (agent, message_id, violation_type), suppress further
# alarms. Catches the persistent-noise tail where a post-cutoff message
# legitimately violates SLA forever (no_retroactive_cleanup commitment
# means cai's pre-Section-A mis-flagged rulings can't be patched).
# 5 fires × 1/hour = 5 hours of operator awareness before tombstone.
_SLA_ALARM_MAX_FIRES = 5


async def check_agent_health(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Run heartbeat staleness check.

    Called every 20 polls (~10 min) from the main orchestrator loop.

    Check-in silence (_check_checkin_silence) AND inbox-SLA P1 alarms
    (check_inbox_sla_violations) are intentionally disabled — operator
    decision: without full automation the CC families don't read their
    inboxes on autopilot, so "agent X hasn't read/responded to msg #N"
    nags are pure noise (a human operator can't action another family's
    unread inbox; agents read when their sessions run). P1 unread still
    surfaces in boot_briefing for in-session agents, so no real signal is
    lost. Heartbeat staleness still runs because it tracks process
    liveness (different signal from coordination cadence).
    """
    await _check_heartbeat_staleness(supabase, bot, musa_chat_id)
    # await _check_checkin_silence(supabase, bot, musa_chat_id)  # disabled — see docstring
    await check_repo_context_health(supabase, bot, musa_chat_id)
    # await check_inbox_sla_violations(supabase, bot, musa_chat_id)  # disabled — fake-autopilot nag (see docstring)


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
            msg = format_alert(
                title=f"{display_name} session looks dead",
                what=(
                    f"Last heartbeat was {stale_str} ago \u2014 the session is "
                    f"either crashed or hung."
                ),
                why=(
                    "Anything that family was supposed to be doing is paused. "
                    "If it had a build job in flight, that job is stalled."
                ),
                do=(
                    "Either reconnect to the existing session or relaunch with "
                    "scripts/launch_dangerous_cc.sh from the family's repo."
                ),
                detail=f"agent_id={agent_id}; warn threshold {_HEARTBEAT_STALE_WARN_MINUTES} min",
                ref="ARCH-022",
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
            msg = format_alert(
                icon="\U0001f4e1",
                title=f"{display_name} hasn't messaged anyone recently",
                what=(
                    f"The session is alive (heartbeat fine) but hasn't sent any "
                    f"agent_message in the last {silence_str}."
                ),
                why=(
                    "Heartbeat means the process is running; check-ins mean the "
                    "agent is coordinating. Long silence suggests it's stuck or "
                    "lost."
                ),
                do=(
                    "Open the session and check what it's doing. ARCH-022 expects "
                    "an update every ~30 min of active work."
                ),
                detail=f"agent_id={agent_id}",
                ref="ARCH-022",
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
# repo_context staleness — CAI-RESP-093 AC-4 /health endpoint surface
# ---------------------------------------------------------------------------

_REPO_CONTEXT_STALE_MINUTES = 60  # 4× the 15-min writer cadence


async def check_repo_context_health(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Telegram alert when public.repo_context is stale > 60 min, indicating
    the repo_context_writer poll loop has not run successfully recently.

    Per CAI-RESP-093 AC-4. Threshold is 4× the writer's 15-min cadence —
    accounts for transient poll failures without false-alerting on every
    network hiccup. Dedup via notification_log key on the hour bucket.
    """
    try:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=_REPO_CONTEXT_STALE_MINUTES)).isoformat()

        # max(updated_at) across all repo_context rows.
        result = await supabase.table("repo_context").select(
            "repo, updated_at"
        ).order("updated_at", desc=True).limit(1).execute()
        rows = result.data or []
        if not rows:
            logger.warning("agent_watchdog: repo_context is empty — writer never ran")
            return  # Empty table: writer hasn't shipped its first sweep yet, do not alert

        max_updated = rows[0].get("updated_at")
        if not max_updated or max_updated >= cutoff:
            return  # Fresh enough.

        try:
            last_dt = datetime.fromisoformat(max_updated.replace("Z", "+00:00"))
            stale_minutes = int((now - last_dt).total_seconds() / 60)
        except ValueError:
            stale_minutes = None

        dedup_key = f"agent_watchdog:repo_context_stale:{_dedup_bucket(now)}"
        if await _check_dedup(supabase, dedup_key):
            return

        stale_str = f"{stale_minutes} min" if stale_minutes else "unknown duration"
        msg = format_alert(
            title="Repo state-tracker has stopped updating",
            what=(
                f"The orchestrator's repo state cache hasn't been refreshed for "
                f"{stale_str} (it normally updates every 15 min)."
            ),
            why=(
                "boot_briefing + repo_context surfaces are now serving stale "
                "data to anything that reads them."
            ),
            do=(
                "Check orch.log for recent 'repo_context_writer' lines — "
                "they'll show whether the writer is crashing or just missing rows."
            ),
            detail=f"Threshold {_REPO_CONTEXT_STALE_MINUTES} min",
            ref="CAI-RESP-093",
        )
        await _send_and_log(
            supabase,
            bot=bot,
            musa_chat_id=musa_chat_id,
            msg=msg,
            source="agent_watchdog.repo_context_stale",
            decision_ref="CAI-RESP-093",
            dedup_key=dedup_key,
        )
    except Exception as e:
        logger.error(f"agent_watchdog repo_context health check failed: {e}")
        error_tracker.track_exception("agent_watchdog.repo_context_check", e)


# ---------------------------------------------------------------------------
# Inbox SLA violations — CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 4
# ---------------------------------------------------------------------------

async def check_inbox_sla_violations(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Telegram alert + notification_log row for P1 inbox_sla_violations.

    Per CAI-PROCESS-INBOX-CADENCE-001 Section D: scheduled-sweep state mutations
    are limited to (a) read_at — NEVER set by sweep, only by the in-session CC,
    (b) notification_log on alarm, (c) boot_briefing surface. P1 unread/
    unresponded → notification_log + Telegram cto_bot. P2/P3 surface in
    boot_briefing only (already wired via Section E Phase 1 view).

    Dedup hour-bucketed per (agent, message_id, violation_type) — fires once
    per hour per offending row to bound alarm rate.

    Filing-date cutoff per CAI-RESP-108: messages created before
    CADENCE_001_FILING_DATE are suppressed (transition-window noise — the
    pre-Section A backlog drains mechanically as agents apply Section A
    discipline forward; alerting on it would spam Musa with non-actionable
    historical violations).

    Per-message tombstone: after _SLA_ALARM_MAX_FIRES alarms have fired for
    the same (agent, message_id, violation_type) tuple, suppress further
    alarms. Persistent post-cutoff violations (e.g. cai's pre-Section-A
    mis-flagged rulings that no_retroactive_cleanup can't fix) burn 24
    Telegrams/day otherwise. 5 fires × 1/hour = 5 hours of operator
    awareness before tombstone.
    """
    try:
        result = await supabase.table("inbox_sla_violations").select(
            "agent, message_id, priority, from_agent, subject, created_at, "
            "violation_type, elapsed_minutes, threshold_minutes"
        ).eq("priority", "P1").gte("created_at", CADENCE_001_FILING_DATE).execute()
        violations = result.data or []
        if not violations:
            return

        now = datetime.now(timezone.utc)
        bucket = _dedup_bucket(now)
        for v in violations:
            agent = v.get("agent")
            msg_id = v.get("message_id")
            vtype = v.get("violation_type")
            dedup_key = f"agent_watchdog:inbox_sla_p1:{agent}:{msg_id}:{vtype}:{bucket}"
            if await _check_dedup(supabase, dedup_key):
                continue

            # Tombstone check: count prior fires for this (agent, msg_id,
            # vtype) tuple. If we've already alerted N times, suppress —
            # the operator has had ample awareness; further fires are noise.
            try:
                tombstone_pattern = f"agent_watchdog:inbox_sla_p1:{agent}:{msg_id}:{vtype}:%"
                prior = (
                    await supabase.table("notification_log")
                    .select("id", count="exact")
                    .like("dedup_key", tombstone_pattern)
                    .execute()
                )
                prior_count = prior.count if hasattr(prior, "count") and prior.count is not None else len(prior.data or [])
                if prior_count >= _SLA_ALARM_MAX_FIRES:
                    logger.debug(
                        f"inbox_sla_p1: tombstoned msg #{msg_id} "
                        f"({prior_count} prior fires >= {_SLA_ALARM_MAX_FIRES} threshold)"
                    )
                    continue
            except Exception as e:
                logger.warning(f"inbox_sla_p1 tombstone check failed for #{msg_id}: {e}")
                # Fail-open: proceed with alarm rather than silently suppress.

            elapsed = v.get("elapsed_minutes")
            threshold = v.get("threshold_minutes")
            from_agent = v.get("from_agent")
            subject = (v.get("subject") or "")[:80]
            action = (
                f"Open {agent}'s session and read msg #{msg_id}."
                if vtype == "unread"
                else f"{agent} should file a substantive response to msg #{msg_id} "
                     f"(open their session)."
            )
            msg = format_alert(
                title=f"{agent} hasn't {vtype} a P1 message in time",
                what=(
                    f"P1 message #{msg_id} from {from_agent} has been "
                    f"{vtype} for {elapsed} min (threshold {threshold} min)."
                ),
                why=(
                    "P1 means time-sensitive — the longer it sits, the more "
                    "likely something downstream is blocked."
                ),
                do=action,
                detail=f"Subject: {subject}",
                ref="CAI-PROCESS-INBOX-CADENCE-001 Section A",
            )
            await _send_and_log(
                supabase,
                bot=bot,
                musa_chat_id=musa_chat_id,
                msg=msg,
                source="agent_watchdog.inbox_sla_p1",
                decision_ref="CAI-PROCESS-INBOX-CADENCE-001",
                dedup_key=dedup_key,
            )
    except Exception as e:
        logger.error(f"agent_watchdog inbox SLA check failed: {e}")
        error_tracker.track_exception("agent_watchdog.inbox_sla_check", e)


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
