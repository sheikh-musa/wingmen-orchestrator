"""Paused Job Escalation — checks for jobs stuck in paused status.

Runs every 30 minutes via the main orchestrator scheduler.
- 1 hour paused -> reminder to CTO chat
- 6 hours paused -> urgent escalation to CTO chat
Deduplicates via notification_log.dedup_key (BUG-002 pattern).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from supabase import AsyncClient as SupabaseAsyncClient
from telegram import Bot

from notification_router import get_chat_id
from nervous_system import error_tracker

logger = logging.getLogger("wingmen.paused_job_escalation")


async def check_paused_jobs(supabase: SupabaseAsyncClient, bot: Bot) -> None:
    """Check for paused jobs that need reminders or escalation."""

    now = datetime.now(timezone.utc)
    one_hour_ago = (now - timedelta(hours=1)).isoformat()
    six_hours_ago = (now - timedelta(hours=6)).isoformat()

    result = await supabase.table("jobs") \
        .select("id, repo_name, description, fail_count, result_summary, updated_at") \
        .eq("status", "paused") \
        .execute()

    paused_jobs = result.data or []

    cto_id = get_chat_id("cto")
    if not cto_id:
        return

    for job in paused_jobs:
        updated = job["updated_at"]

        if updated < six_hours_ago:
            dedup_key = f"paused_escalation:{job['id']}:urgent"
            try:
                existing = await supabase.table("notification_log").select("id").eq(
                    "dedup_key", dedup_key
                ).limit(1).execute()
                if existing.data:
                    continue
            except Exception as e:
                logger.warning(f"Dedup check failed for {dedup_key}: {e}")
                error_tracker.track_exception("paused_job_escalation.dedup_check", e)

            try:
                await bot.send_message(
                    chat_id=cto_id,
                    text=(
                        f"\U0001f534 Job #{job['id']} still paused after 6h+\n\n"
                        f"Repo: {job['repo_name']}\n"
                        f"Task: {(job.get('description') or '')[:200]}\n"
                        f"Failures: {job.get('fail_count', 0)}\n"
                        f"Last error: {(job.get('result_summary') or '')[:300]}\n\n"
                        f"/resume {job['id']} or /cancel {job['id']}"
                    ),
                )
            except Exception as e:
                logger.error(f"Urgent escalation for job {job['id']} failed: {e}")
                error_tracker.track_exception("paused_job_escalation.urgent_send", e)
                continue

            try:
                await supabase.table("notification_log").insert({
                    "source": "paused_job_escalation",
                    "channel": "telegram",
                    "recipient": cto_id,
                    "message_text": f"Urgent: Job #{job['id']} paused 6h+",
                    "dedup_key": dedup_key,
                }).execute()
            except Exception as e:
                logger.error(f"Failed to log notification for job {job['id']}: {e}")
                error_tracker.track_exception("paused_job_escalation.log_notification", e)

        elif updated < one_hour_ago:
            dedup_key = f"paused_escalation:{job['id']}:reminder"
            try:
                existing = await supabase.table("notification_log").select("id").eq(
                    "dedup_key", dedup_key
                ).limit(1).execute()
                if existing.data:
                    continue
            except Exception as e:
                logger.warning(f"Dedup check failed for {dedup_key}: {e}")
                error_tracker.track_exception("paused_job_escalation.dedup_check", e)

            try:
                await bot.send_message(
                    chat_id=cto_id,
                    text=(
                        f"\u23f8\ufe0f Job #{job['id']} paused for 1h+ \u2014 needs attention\n\n"
                        f"Repo: {job['repo_name']}\n"
                        f"Task: {(job.get('description') or '')[:200]}\n"
                        f"Failures: {job.get('fail_count', 0)}\n\n"
                        f"/resume {job['id']} or /cancel {job['id']}"
                    ),
                )
            except Exception as e:
                logger.error(f"Reminder for job {job['id']} failed: {e}")
                error_tracker.track_exception("paused_job_escalation.reminder_send", e)
                continue

            try:
                await supabase.table("notification_log").insert({
                    "source": "paused_job_escalation",
                    "channel": "telegram",
                    "recipient": cto_id,
                    "message_text": f"Reminder: Job #{job['id']} paused 1h+",
                    "dedup_key": dedup_key,
                }).execute()
            except Exception as e:
                logger.error(f"Failed to log notification for job {job['id']}: {e}")
                error_tracker.track_exception("paused_job_escalation.log_notification", e)

    if paused_jobs:
        logger.info(f"Checked {len(paused_jobs)} paused jobs")
