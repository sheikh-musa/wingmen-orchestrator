"""Approval Handler — routes bug fix approvals to eligible Telegram users.

Determines who can approve based on confidence level and role:
- super_admin: any repo, any confidence
- support: any repo, high + medium only
- org_admin: ihsanOS bugs for their org only, high only
"""

from __future__ import annotations

import logging
import os
from typing import NamedTuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("wingmen.approval")

MUSA_TELEGRAM_ID = os.environ.get("MUSA_TELEGRAM_ID", "")


class Approver(NamedTuple):
    chat_id: str
    name: str
    role: str  # super_admin, support, org_admin


async def get_eligible_approvers(
    supabase,
    bug_report: dict,
) -> list[Approver]:
    """Determine who can approve this bug fix based on confidence and repo."""
    confidence = bug_report.get("confidence", "low")
    approvers = []

    # Super admin (Musa) — always eligible
    if MUSA_TELEGRAM_ID:
        approvers.append(Approver(
            chat_id=MUSA_TELEGRAM_ID,
            name="Musa",
            role="super_admin",
        ))

    # Support admins — eligible for high + medium
    if confidence in ("high", "medium"):
        # Look up platform_admins with support role who have telegram
        # For now, check clients table for support users
        # This will be expanded when platform_admins has telegram_chat_id
        pass  # TODO: query platform_admins when telegram_chat_id is available

    # Org admins — only for ihsanOS, high confidence only
    # TODO: implement when org_admin telegram linking is available

    return approvers


def build_approval_message(bug: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Build the Telegram approval message with inline keyboard."""

    confidence_emoji = {"high": "\U0001f7e2", "medium": "\U0001f7e1", "low": "\U0001f534"}.get(
        bug.get("confidence") or "", "\u26aa"
    )

    # Compact message
    lines = [
        f"\U0001f41b Bug \u2014 {bug['repo_name']}",
        "",
        f"Reporter: {bug['reporter_name']} ({bug['reporter_source']})",
        f"Issue: {bug['description'][:200]}",
        f"Root cause: {bug.get('root_cause') or 'unknown'}",
        f"Confidence: {confidence_emoji} {(bug.get('confidence') or 'unknown').title()}",
        f"Files: {', '.join(bug.get('affected_files') or ['unknown'])}",
    ]

    # Add compact diff (first 500 chars)
    diff = bug.get("proposed_diff", "")
    if diff:
        lines.append("")
        lines.append("\u2501" * 20)
        diff_preview = diff[:500]
        if len(diff) > 500:
            diff_preview += "\n... (truncated)"
        lines.append(diff_preview)
        lines.append("\u2501" * 20)

    text = "\n".join(lines)

    # Inline keyboard
    bug_id = bug["id"]
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Full Diagnosis", callback_data=f"bug_diag:{bug_id}"),
        ],
        [
            InlineKeyboardButton("Approve \u2713", callback_data=f"bug_approve:{bug_id}"),
            InlineKeyboardButton("Reject \u2717", callback_data=f"bug_reject:{bug_id}"),
            InlineKeyboardButton("Handle Myself", callback_data=f"bug_escalate:{bug_id}"),
        ],
    ])

    return text, keyboard


def build_full_diagnosis_message(bug: dict) -> str:
    """Build the expanded diagnosis message."""
    return (
        f"\U0001f4cb Full Diagnosis \u2014 {bug['repo_name']}\n\n"
        f"{bug.get('diagnosis_full') or 'No detailed diagnosis available.'}\n\n"
        f"Affected files:\n"
        + "\n".join(f"  \u2022 {f}" for f in (bug.get("affected_files") or []))
    )


def build_verification_keyboard(bug_id: str) -> InlineKeyboardMarkup:
    """Build verification buttons for the reporter."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Verified \u2713", callback_data=f"bug_verified:{bug_id}"),
            InlineKeyboardButton("Still broken", callback_data=f"bug_still_broken:{bug_id}"),
        ],
    ])
