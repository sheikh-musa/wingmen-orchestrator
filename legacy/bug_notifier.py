"""Bug Notifier — sends notifications to reporters and approvers.

Supports two notification channels:
- Telegram: for reporters who filed via Telegram bot
- Email (via Resend): for reporters who filed via the web form
"""

from __future__ import annotations

import logging
import os
from typing import Optional

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


# ── Email Notifications (Resend) ─────────────────────────────────


async def notify_reporter_by_email(email: str, subject: str, body_html: str) -> None:
    """Send email notification to web-based reporter via Resend."""
    import httpx

    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        logger.warning("RESEND_API_KEY not set, skipping email notification")
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}"},
                json={
                    "from": "ihsanOS <noreply@ihsanos.com>",
                    "to": email,
                    "subject": subject,
                    "html": body_html,
                },
            )
            resp.raise_for_status()
            logger.info(f"Email sent to {email}: {subject}")
    except Exception as e:
        logger.error(f"Email notification failed: {e}")


# ── Smart Routing ─────────────────────────────────────────────────


async def notify_reporter_smart(
    bot: Bot,
    bug: dict,
    subject: str,
    telegram_text: str,
    email_html: str,
    verification_keyboard=None,
) -> None:
    """Route notification to the right channel based on reporter_source."""
    source = bug.get("reporter_source", "telegram")

    if source == "web" and bug.get("reporter_email"):
        await notify_reporter_by_email(bug["reporter_email"], subject, email_html)
    elif bug.get("reporter_telegram_chat_id"):
        kwargs: dict = {"chat_id": bug["reporter_telegram_chat_id"], "text": telegram_text}
        if verification_keyboard:
            kwargs["reply_markup"] = verification_keyboard
        await bot.send_message(**kwargs)
    else:
        logger.warning(f"No notification channel for bug {bug.get('id')} (source={source})")
