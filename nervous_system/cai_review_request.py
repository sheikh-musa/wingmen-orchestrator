"""
cai_review_request.py — Request cai review of a CC decision via Telegram.

When Claude Code writes a decision that needs strategic review, this module
sends a Telegram notification to Musa asking him to open claude.ai.
Cai auto-queries strategic_decisions on first message and sees the pending review.

Part of ARCH-005: CC-to-cai review requests.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("wingmen.cai_review_request")


async def request_cai_review(
    supabase,
    decision_ref: str,
    title: str,
    reason: str,
    bot=None,
    musa_chat_id: str | None = None,
):
    """Send a cai review request to Musa via Telegram.

    Called by CC (via strategic_decisions insert) when a decision
    has strategic implications that need cai's input.
    """
    # Tag the decision as needing cai review
    try:
        await supabase.table("strategic_decisions").update(
            {"challenge_status": "cai_review_requested"}
        ).eq("decision_ref", decision_ref).execute()
    except Exception as e:
        logger.error(f"Failed to tag {decision_ref} for cai review: {e}")
        return

    # Notify Musa via Telegram
    if bot and musa_chat_id:
        msg = (
            f"🔍 cai review needed\n\n"
            f"{decision_ref}: {title}\n\n"
            f"Reason: {reason}\n\n"
            f"Open claude.ai when you have a moment."
        )
        try:
            await bot.send_message(
                chat_id=musa_chat_id,
                text=msg,
            )
            logger.info(f"cai review request sent for {decision_ref}")
        except Exception as e:
            logger.error(f"Failed to send cai review Telegram for {decision_ref}: {e}")
    else:
        logger.warning(f"cai review requested for {decision_ref} but no Telegram bot available")


async def poll_cai_review_requests(supabase, bot=None, musa_chat_id: str | None = None):
    """Poll for decisions needing cai review that haven't been notified yet.

    Called every 10 polls (~5 min) from the main orchestrator loop,
    same cadence as strategic_decisions_poll.
    """
    try:
        result = await supabase.table("strategic_decisions").select(
            "id, decision_ref, title, reasoning, category, parent_ref"
        ).eq("source", "claude_code_proposal").eq(
            "challenge_status", "cai_review_requested"
        ).is_(
            "notified_at", "null"
        ).order("created_at", desc=False).execute()

        rows = result.data or []
        if not rows:
            return

        logger.info(f"cai_review_request: found {len(rows)} pending review requests")

        for row in rows:
            ref = row["decision_ref"]
            title = row["title"]
            reason = (row.get("reasoning") or "")[:200]

            if bot and musa_chat_id:
                msg = (
                    f"🔍 cai review needed\n\n"
                    f"{ref}: {title}\n\n"
                    f"{reason}{'...' if len(row.get('reasoning', '')) > 200 else ''}\n\n"
                    f"Open claude.ai when you have a moment."
                )
                try:
                    sent = await bot.send_message(chat_id=musa_chat_id, text=msg)
                    # Log to notification_log with dedup_key so re-sends are idempotent
                    # (cai flagged this gap: prior rows had dedup_key=null).
                    try:
                        await supabase.table("notification_log").insert({
                            "source": "cai_review_request",
                            "decision_ref": ref,
                            "channel": "telegram",
                            "recipient": musa_chat_id or "unknown",
                            "message_text": msg,
                            "telegram_msg_id": sent.message_id,
                            "dedup_key": f"decision:{ref}:cai_review",
                        }).execute()
                    except Exception as log_e:
                        logger.error(f"Failed to log notification for {ref}: {log_e}")
                except Exception as e:
                    logger.error(f"Failed to send cai review Telegram for {ref}: {e}")

            # Mark as notified
            try:
                await supabase.table("strategic_decisions").update(
                    {"notified_at": datetime.now(timezone.utc).isoformat()}
                ).eq("id", row["id"]).execute()
            except Exception as e:
                logger.error(f"Failed to mark {ref} as notified: {e}")

    except Exception as e:
        logger.error(f"cai_review_request poll failed: {e}")
