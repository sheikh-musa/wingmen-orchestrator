"""
strategic_decisions_poll.py — Auto-implement cai decisions.

When cai writes a decision with challenge_status='challenge_window',
this module auto-queues a Claude Code work session to implement it.
Musa is notified when the job COMPLETES, not when it's found.

ARCH-004: close the cai→CC notification gap.
BUG-001: notified_at prevents duplicates.
ARCH-007: logs to notification_log for cai visibility.
BUG-002: prefix filter — only auto-queue TASK-*, BUG-*, IMPL-*, CAI-RESP-*.
         ARCH-*, FEE-*, PROD-* notify only, wait for Musa ruling.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("wingmen.strategic_decisions_poll")

# BUG-002: Decision refs with these prefixes auto-queue jobs.
# Everything else (ARCH-*, FEE-*, PROD-*, etc.) notifies Musa and waits for ruling.
AUTO_QUEUE_PREFIXES = ("TASK-", "BUG-", "IMPL-", "CAI-RESP-")


def _should_auto_queue(decision_ref: str) -> bool:
    """Return True if this decision's prefix is allowed to auto-queue a job."""
    return decision_ref.startswith(AUTO_QUEUE_PREFIXES)


async def poll_strategic_decisions(supabase, bot=None, musa_chat_id: str | None = None):
    """Find new cai decisions and auto-queue work sessions to implement them.

    Called every 10 polls (~5 min) from the main orchestrator loop.
    """
    try:
        result = await supabase.table("strategic_decisions").select(
            "id, decision_ref, title, decision, reasoning, repos_affected, challenge_status, created_at"
        ).eq("source", "claude_ai_session").eq(
            "challenge_status", "challenge_window"
        ).is_(
            "notified_at", "null"
        ).order("created_at", desc=False).execute()

        rows = result.data or []
        if not rows:
            return

        logger.info(f"strategic_decisions_poll: found {len(rows)} new decisions — auto-queuing jobs")

        for row in rows:
            ref = row["decision_ref"]
            title = row["title"]
            decision = row["decision"]
            reasoning = row.get("reasoning") or ""
            repos = row.get("repos_affected") or []

            # BUG-002: prefix filter — gate ARCH-*, FEE-*, PROD-* behind Musa approval
            if not _should_auto_queue(ref):
                logger.info(f"  -> {ref}: prefix not auto-queueable, notifying Musa only")
                await _notify_musa_and_wait(
                    supabase, row, bot, musa_chat_id
                )
                await _mark_notified(supabase, row["id"], ref)
                continue

            if not repos:
                logger.warning(f"  {ref}: no repos_affected, skipping")
                await _mark_notified(supabase, row["id"], ref)
                continue

            repo = repos[0]  # Primary repo for the job

            # Build the session prompt from the decision
            session_prompt = (
                f"Strategic decision from cai: {ref}\n\n"
                f"Title: {title}\n\n"
                f"Decision: {decision}\n\n"
                f"Reasoning: {reasoning}\n\n"
                f"Instructions:\n"
                f"1. Review this decision against the codebase\n"
                f"2. If it conflicts with implementation reality, write a challenge "
                f"to strategic_decisions with challenge_status='challenged'\n"
                f"3. If it holds, accept it and implement the changes\n"
                f"4. Write results back to strategic_decisions as an IMPL- decision\n"
                f"5. Update repo_context after completing work"
            )

            # Queue a job for the orchestrator to pick up
            try:
                job_result = await supabase.table("jobs").insert({
                    "repo_name": repo,
                    "description": f"[{ref}] {title}",
                    "session_prompt": session_prompt,
                    "priority": 2,  # High priority — strategic decision
                    "triggered_by": "strategic_decisions_poll",
                    "status": "queued",
                }).execute()

                job_id = job_result.data[0]["id"] if job_result.data else None
                logger.info(f"  -> {ref}: queued job {job_id} for repo {repo}")

            except Exception as e:
                logger.error(f"Failed to queue job for {ref}: {e}")
                continue

            # Log to notification_log (ARCH-007) with dedup_key to prevent double-queue
            try:
                await supabase.table("notification_log").insert({
                    "source": "strategic_decisions_poll",
                    "decision_ref": ref,
                    "channel": "job_queue",
                    "recipient": f"orchestrator/jobs/{job_id}",
                    "message_text": f"Auto-queued job {job_id} for {repo}: {title}",
                    "dedup_key": f"decision:{ref}:queued",
                }).execute()
            except Exception as e:
                logger.error(f"Failed to log notification for {ref}: {e}")

            # Mark as notified — prevents re-queuing on next poll
            await _mark_notified(supabase, row["id"], ref)

    except Exception as e:
        logger.error(f"strategic_decisions_poll failed: {e}")


async def notify_decision_complete(
    supabase,
    decision_ref: str,
    job_id: int,
    success: bool,
    summary: str,
    bot=None,
    musa_chat_id: str | None = None,
):
    """Notify Musa when a strategic decision job completes.

    Called by the orchestrator after a strategic_decisions_poll job finishes.
    BUG-002: dedups via dedup_key to prevent duplicate notifications on restart.
    """
    # BUG-002 dedup
    dedup_key = f"decision:{decision_ref}:complete:{job_id}"
    try:
        existing = await supabase.table("notification_log").select("id").eq(
            "dedup_key", dedup_key
        ).limit(1).execute()
        if existing.data:
            logger.info(f"Skipping duplicate completion notification: {dedup_key}")
            return
    except Exception as e:
        logger.warning(f"Dedup check failed for {dedup_key}: {e}")

    status_emoji = "✅" if success else "❌"
    msg = (
        f"{status_emoji} {decision_ref} {'implemented' if success else 'failed'}\n\n"
        f"{summary[:500]}"
    )

    if bot and musa_chat_id:
        try:
            await bot.send_message(chat_id=musa_chat_id, text=msg)
        except Exception as e:
            logger.error(f"Failed to notify completion for {decision_ref}: {e}")

    # Log completion notification with dedup_key
    try:
        await supabase.table("notification_log").insert({
            "source": "strategic_decisions_complete",
            "decision_ref": decision_ref,
            "channel": "telegram",
            "recipient": musa_chat_id or "unknown",
            "message_text": msg,
            "dedup_key": dedup_key,
        }).execute()
    except Exception as e:
        logger.error(f"Failed to log completion notification for {decision_ref}: {e}")


async def _notify_musa_and_wait(supabase, row: dict, bot=None, musa_chat_id: str | None = None):
    """BUG-002: For ARCH-*, FEE-*, PROD-* prefixes — notify Musa, don't auto-queue.
    Waits for Musa ruling before any job is created.
    """
    ref = row["decision_ref"]
    title = row["title"]
    decision = row["decision"][:300]

    # Dedup
    dedup_key = f"decision:{ref}:notify_only"
    try:
        existing = await supabase.table("notification_log").select("id").eq(
            "dedup_key", dedup_key
        ).limit(1).execute()
        if existing.data:
            logger.info(f"Skipping duplicate notify-only: {dedup_key}")
            return
    except Exception as e:
        logger.warning(f"Dedup check failed for {dedup_key}: {e}")

    msg = (
        f"Strategic decision needs your ruling\n\n"
        f"{ref}: {title}\n\n"
        f"{decision}{'...' if len(row['decision']) > 300 else ''}\n\n"
        f"Prefix {ref.split('-')[0]}-* requires Musa approval before auto-execution.\n"
        f"Reply with a ruling or use /rule to queue manually."
    )

    if bot and musa_chat_id:
        try:
            sent = await bot.send_message(chat_id=musa_chat_id, text=msg)
            tg_msg_id = sent.message_id
        except Exception as e:
            logger.error(f"Failed to notify-and-wait for {ref}: {e}")
            tg_msg_id = None
    else:
        tg_msg_id = None

    # Log with dedup_key
    try:
        await supabase.table("notification_log").insert({
            "source": "strategic_decisions_notify_only",
            "decision_ref": ref,
            "channel": "telegram",
            "recipient": musa_chat_id or "unknown",
            "message_text": msg,
            "telegram_msg_id": tg_msg_id,
            "dedup_key": dedup_key,
        }).execute()
    except Exception as e:
        logger.error(f"Failed to log notify-only for {ref}: {e}")


async def _mark_notified(supabase, row_id: int, ref: str):
    """Mark a strategic_decisions row as notified."""
    try:
        await supabase.table("strategic_decisions").update(
            {"notified_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", row_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark {ref} as notified: {e}")
