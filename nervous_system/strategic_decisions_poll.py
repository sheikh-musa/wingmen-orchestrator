"""
strategic_decisions_poll.py — Auto-implement cai decisions.

Polls strategic_decisions for decisions that passed mutual review
(ARCH-013) and auto-queues Claude Code work sessions to implement them.
Musa is notified via Telegram when the job COMPLETES, not when found.

ARCH-004: close the cai→CC notification gap (Option B — 5-min poll).
BUG-001: notified_at prevents duplicates.
ARCH-007: logs to notification_log for cai visibility.
ARCH-013: mutual-review gate replaces prefix-based gating (old BUG-002).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("wingmen.strategic_decisions_poll")

async def poll_strategic_decisions(supabase, bot=None, musa_chat_id: str | None = None):
    """Find decisions ready for execution under ARCH-013 mutual-review.

    Queue criteria (any ONE of these):
      - source=claude_ai_session AND challenge_status=accepted
        → cai wrote it, CC reviewed and agreed
      - source=claude_code_proposal AND challenge_status=accepted
        → CC proposed it, cai reviewed and agreed
      - source=musa_direct
        → Musa ruled, binding
      - bypass_review=true
        → emergency escape (only musa_direct can set this)

    CAI-RESP-* with challenge_status=challenge_window also auto-queues — these
    are responses to CC challenges, not new work proposals; adversarial
    review already happened in the original challenge.

    Called every 10 polls (~5 min) from the main orchestrator loop.
    """
    try:
        # ARCH-013: mutual-review-or-musa_direct-or-bypass
        # Build an OR filter:
        #   (source=claude_ai_session + status=accepted) OR
        #   (source=claude_code_proposal + status=accepted) OR
        #   (source=musa_direct) OR
        #   (bypass_review=true) OR
        #   (ref LIKE 'CAI-RESP-%' + status=challenge_window)
        result = await supabase.table("strategic_decisions").select(
            "id, decision_ref, title, decision, reasoning, repos_affected, challenge_status, source, bypass_review, created_at"
        ).or_(
            "and(source.eq.claude_ai_session,challenge_status.eq.accepted),"
            "and(source.eq.claude_code_proposal,challenge_status.eq.accepted),"
            "source.eq.musa_direct,"
            "bypass_review.eq.true,"
            "and(decision_ref.like.CAI-RESP-%,challenge_status.eq.challenge_window)"
        ).is_(
            "notified_at", "null"
        ).order("created_at", desc=False).execute()

        rows = result.data or []
        if not rows:
            return

        logger.info(f"strategic_decisions_poll: found {len(rows)} ready decisions — queuing jobs")

        for row in rows:
            ref = row["decision_ref"]
            title = row["title"]
            decision = row["decision"]
            reasoning = row.get("reasoning") or ""
            repos = row.get("repos_affected") or []

            # ARCH-013 + CAI-RESP-007: no prefix-based gating anymore. If we
            # reach here, the decision has already passed the mutual-review
            # gate (or bypass_review=true, or it's a CAI-RESP response).
            # Musa isn't the gate now — the DB is.

            if not repos:
                logger.warning(f"  {ref}: no repos_affected, skipping")
                await _mark_notified(supabase, row["id"], ref)
                continue

            repo = repos[0]  # Primary repo for the job

            # Build the session prompt. Under ARCH-013, the review already
            # happened before this queued — just implement.
            source_label = {
                "claude_ai_session": "cai (reviewed + accepted by CC)",
                "claude_code_proposal": "CC (reviewed + accepted by cai)",
                "musa_direct": "Musa (binding ruling)",
            }.get(row.get("source", ""), row.get("source", "unknown"))
            bypass_note = " [bypass_review=true — emergency]" if row.get("bypass_review") else ""
            session_prompt = (
                f"Strategic decision from {source_label}: {ref}{bypass_note}\n\n"
                f"Title: {title}\n\n"
                f"Decision: {decision}\n\n"
                f"Reasoning: {reasoning}\n\n"
                f"Instructions:\n"
                f"1. Implement the decision. Mutual review already passed.\n"
                f"2. If you discover a real blocker mid-implementation, stop and "
                f"write an IMPL- decision explaining the blocker (do NOT challenge "
                f"the original — that ship sailed at review time).\n"
                f"3. Write deliverables to work_outputs (ARCH-010) and a narrative "
                f"row to cc_work_sessions (BUG-006 fix).\n"
                f"4. Update repo_context after completing work.\n"
                f"5. On success, the orchestrator will auto-flip the source decision "
                f"to 'implemented' (once TASK-026 lands). Until then, flip it yourself."
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


async def _mark_notified(supabase, row_id: int, ref: str):
    """Mark a strategic_decisions row as notified."""
    try:
        await supabase.table("strategic_decisions").update(
            {"notified_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", row_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark {ref} as notified: {e}")
