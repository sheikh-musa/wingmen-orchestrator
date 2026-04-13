"""Bug Escalation — checks for stale bug reports and sends reminders/escalations.

Runs every 30 minutes via the main orchestrator scheduler.
- 4 hours with no response -> reminder to approvers
- 24 hours with no response -> escalate to super_admin only
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from supabase import AsyncClient as SupabaseAsyncClient
from telegram import Bot

from notification_router import get_chat_id

logger = logging.getLogger("wingmen.bug_escalation")


async def check_stale_bugs(supabase: SupabaseAsyncClient, bot: Bot) -> None:
    """Check for bug reports that need reminders or escalation."""

    now = datetime.now(timezone.utc)
    four_hours_ago = (now - timedelta(hours=4)).isoformat()
    twenty_four_hours_ago = (now - timedelta(hours=24)).isoformat()

    # Find bugs in "proposed" status (waiting for approval)
    result = await supabase.table("bug_reports") \
        .select("id, repo_name, reporter_name, description, confidence, approval_sent_to, created_at") \
        .eq("status", "proposed") \
        .execute()

    stale_bugs = result.data or []

    for bug in stale_bugs:
        created = bug["created_at"]

        if created < twenty_four_hours_ago:
            # 24h+ — escalate to super admin
            logger.warning(f"Bug {bug['id']} stale for 24h+, escalating to super admin")

            cto_id = get_chat_id("cto")
            if cto_id:
                try:
                    await bot.send_message(
                        chat_id=cto_id,
                        text=(
                            f"Bug waiting 24h+ for approval\n\n"
                            f"Repo: {bug['repo_name']}\n"
                            f"Reporter: {bug['reporter_name']}\n"
                            f"Issue: {bug['description'][:200]}\n"
                            f"Confidence: {bug['confidence']}\n\n"
                            f"This needs your attention."
                        ),
                    )
                except Exception as e:
                    logger.error(f"Escalation notification failed: {e}")

        elif created < four_hours_ago:
            # 4h+ — send reminder to all approvers
            approvers = bug.get("approval_sent_to") or []
            for chat_id in approvers:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"Reminder: Bug in {bug['repo_name']} is waiting for your review.\n\nIssue: {bug['description'][:150]}",
                    )
                except Exception as e:
                    logger.error(f"Reminder to {chat_id} failed: {e}")

    if stale_bugs:
        logger.info(f"Checked {len(stale_bugs)} stale bugs")
