"""Queue Stall Detector — checks for jobs stuck in queued status.

Runs every 30 minutes via the main orchestrator scheduler.
- 30 minutes queued -> alert to CTO chat
Deduplicates via notification_log.dedup_key.
Clears dedup records when jobs leave queued status (recovery sweep).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from supabase import AsyncClient as SupabaseAsyncClient
from telegram import Bot

from notification_router import get_chat_id

logger = logging.getLogger("wingmen.queue_stall_detector")


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

        try:
            await bot.send_message(
                chat_id=cto_id,
                text=(
                    f"\u23f3 Job #{job['id']} queued for 30min+ \u2014 not being picked up\n\n"
                    f"Repo: {job['repo_name']}\n"
                    f"Task: {(job.get('description') or '')[:200]}\n"
                    f"Priority: {job.get('priority', 'N/A')}\n"
                    f"Queued since: {job['created_at']}\n\n"
                    f"/cancel {job['id']} to remove from queue"
                ),
            )
        except Exception as e:
            logger.error(f"Queue stall alert for job {job['id']} failed: {e}")
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

    if stalled_count:
        logger.info(f"Checked {stalled_count} stalled queued jobs")
