"""Bug Notifier — sends notifications to reporters and approvers."""

from __future__ import annotations

import logging
from telegram import Bot

logger = logging.getLogger("wingmen.bug_notifier")


async def notify_reporter_acknowledged(bot: Bot, chat_id: str, bug_id: str) -> None:
    """Tell the reporter we received their bug report."""
    await bot.send_message(
        chat_id=chat_id,
        text="\U0001f50d Got it \u2014 I'm diagnosing this now. I'll notify you when there's a fix.",
    )


async def notify_reporter_deployed(
    bot: Bot,
    chat_id: str,
    bug: dict,
    verification_keyboard,
) -> None:
    """Tell the reporter the fix is deployed and ask for verification."""
    deploy_url = bug.get("deploy_url", "")
    url_line = f"\n\U0001f517 Verify here: {deploy_url}" if deploy_url else ""

    await bot.send_message(
        chat_id=chat_id,
        text=f"\U0001f389 Your bug has been fixed and deployed!{url_line}\n\nPlease verify \u2014 does it work now?",
        reply_markup=verification_keyboard,
    )


async def notify_reporter_rejected(bot: Bot, chat_id: str, reason: str | None) -> None:
    """Tell the reporter we're investigating differently."""
    msg = "We're investigating this issue differently."
    if reason:
        msg += f"\n\nNote: {reason}"
    await bot.send_message(chat_id=chat_id, text=msg)


async def notify_approvers(
    bot: Bot,
    approvers: list,
    message_text: str,
    keyboard,
    supabase,
    bug_id: str,
) -> None:
    """Send approval messages to all eligible approvers."""
    sent_to = []

    for approver in approvers:
        try:
            await bot.send_message(
                chat_id=approver.chat_id,
                text=message_text,
                reply_markup=keyboard,
            )
            sent_to.append(approver.chat_id)
            logger.info(f"Approval sent to {approver.name} ({approver.chat_id})")
        except Exception as e:
            logger.error(f"Failed to notify approver {approver.name}: {e}")

    # Store which chat IDs received the message
    if sent_to:
        await supabase.table("bug_reports").update({
            "approval_sent_to": sent_to,
        }).eq("id", bug_id).execute()
