"""Queue Stall Detector — checks for jobs stuck in queued status.

Runs every 30 minutes via the main orchestrator scheduler.
- 30 minutes queued -> alert to CTO chat
Deduplicates via notification_log.dedup_key.
Clears dedup records when jobs leave queued status (recovery sweep).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

from supabase import AsyncClient as SupabaseAsyncClient
from telegram import Bot

from notification_router import get_chat_id
from nervous_system import error_tracker
from nervous_system.alert_format import format_alert

logger = logging.getLogger("wingmen.queue_stall_detector")


def _ralph_runner_enabled() -> bool:
    """Mirrors wingmen_orch._ralph_runner_enabled. When false, the queue-stall
    alert text reads as 'expected during paused mode' rather than 'malfunction'."""
    return os.environ.get("RALPH_RUNNER_ENABLED", "true").lower() not in (
        "false", "0", "no", "off",
    )


async def check_queue_stalls(supabase: SupabaseAsyncClient, bot: Bot) -> None:
    """Check for jobs stuck in queued status longer than 30 minutes."""

    now = datetime.now(timezone.utc)
    thirty_min_ago = (now - timedelta(minutes=30)).isoformat()

    result = await supabase.table("jobs") \
        .select("id, repo_name, description, priority, created_at") \
        .eq("status", "queued") \
        .execute()

    queued_jobs = result.data or []

    cto_id = get_chat_id("cto")
    if not cto_id:
        return

    stalled_count = 0
    for job in queued_jobs:
        if job["created_at"] >= thirty_min_ago:
            continue

        stalled_count += 1
        dedup_key = f"queue_stall:{job['id']}"

        try:
            existing = await supabase.table("notification_log").select("id").eq(
                "dedup_key", dedup_key
            ).limit(1).execute()
            if existing.data:
                continue
        except Exception as e:
            logger.warning(f"Dedup check failed for {dedup_key}: {e}")
            error_tracker.track_exception("queue_stall_detector.dedup_check", e)

        ralphy_paused = not _ralph_runner_enabled()
        if ralphy_paused:
            what = (
                f"Job #{job['id']} ({job['repo_name']}) has been queued for "
                f"30+ min \u2014 ralphy is currently paused via "
                f"RALPH_RUNNER_ENABLED=false, so this is expected."
            )
            why = (
                "The bug fix won't auto-run until ralphy resumes. "
                "Surfacing so it doesn't get forgotten in the queue."
            )
            do = (
                f"Either fix manually via the {job['repo_name']} CC family "
                f"session, OR resume ralphy: flip RALPH_RUNNER_ENABLED=true "
                f"in .env + restart orchestrator + the job claims on next poll."
            )
        else:
            what = (
                f"Job #{job['id']} ({job['repo_name']}) has been queued for "
                f"30+ min and ralphy hasn't picked it up \u2014 this is unusual."
            )
            why = (
                "Either no build slots are free (3-concurrent cap), or ralphy "
                "hit a transient issue claiming. Either way the bug fix is stalled."
            )
            do = (
                f"Check the orchestrator logs for ralph claim activity. "
                f"`/cancel {job['id']}` to remove from queue if intentional."
            )

        alert_text = format_alert(
            icon="\u23f3",  # \u23f3
            title=f"Job #{job['id']} stuck in queue",
            what=what,
            why=why,
            do=do,
            detail=(
                f"Repo: {job['repo_name']}; priority: {job.get('priority', 'N/A')}; "
                f"queued since: {job['created_at']}; "
                f"task: {(job.get('description') or '')[:140]}"
            ),
        )

        try:
            await bot.send_message(chat_id=cto_id, text=alert_text)
        except Exception as e:
            logger.error(f"Queue stall alert for job {job['id']} failed: {e}")
            error_tracker.track_exception("queue_stall_detector.send_alert", e)
            continue

        try:
            await supabase.table("notification_log").insert({
                "source": "queue_stall_detector",
                "channel": "telegram",
                "recipient": cto_id,
                "message_text": f"Queue stall: Job #{job['id']} queued 30min+",
                "dedup_key": dedup_key,
            }).execute()
        except Exception as e:
            logger.error(f"Failed to log notification for job {job['id']}: {e}")
            error_tracker.track_exception("queue_stall_detector.log_notification", e)

    # Recovery sweep: clear dedup keys for jobs no longer queued
    try:
        dedup_rows = await supabase.table("notification_log").select("id, dedup_key").eq(
            "source", "queue_stall_detector"
        ).execute()

        for row in (dedup_rows.data or []):
            key = row["dedup_key"]
            if not key.startswith("queue_stall:"):
                continue
            job_id = key.split(":", 1)[1]
            job_check = await supabase.table("jobs").select("status").eq(
                "id", job_id
            ).limit(1).execute()
            if job_check.data and job_check.data[0]["status"] != "queued":
                await supabase.table("notification_log").delete().eq(
                    "id", row["id"]
                ).execute()
    except Exception as e:
        logger.warning(f"Recovery sweep failed: {e}")
        error_tracker.track_exception("queue_stall_detector.recovery_sweep", e)

    if stalled_count:
        logger.info(f"Checked {stalled_count} stalled queued jobs")
