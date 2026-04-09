"""Invite Manager — generates and manages team invite codes.

Codes are 8 chars, single-use, expire in 24 hours.
Deep links: t.me/{bot_username}?start=invite_{code}
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("wingmen.invite_manager")


def generate_invite_code() -> str:
    """Generate 8-char alphanumeric invite code."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def build_deep_link(bot_username: str, invite_code: str) -> str:
    """Build Telegram deep link for invite."""
    return f"https://t.me/{bot_username}?start=invite_{invite_code}"


async def create_invite(
    supabase,
    client_id: int,
    invited_name: str,
    role: str,
    added_by_id: int,
    bot_username: str,
) -> dict:
    """Create a pending invite. Returns {code, deep_link, expires_at}."""
    code = generate_invite_code()
    expires = datetime.now(timezone.utc) + timedelta(hours=24)

    await supabase.table("bot_users").insert({
        "client_id": client_id,
        "telegram_chat_id": f"pending_{code}",  # placeholder until claimed
        "name": invited_name,
        "role": role,
        "status": "pending",
        "invite_code": code,
        "invite_expires_at": expires.isoformat(),
        "added_by": added_by_id,
    }).execute()

    deep_link = build_deep_link(bot_username, code)

    logger.info(f"Invite created: {invited_name} as {role} for client {client_id}")
    return {
        "code": code,
        "deep_link": deep_link,
        "expires_at": expires.isoformat(),
    }


async def cleanup_expired_invites(supabase) -> int:
    """Delete expired pending invites. Returns count deleted."""
    now = datetime.now(timezone.utc).isoformat()

    result = await supabase.table("bot_users") \
        .delete() \
        .eq("status", "pending") \
        .lt("invite_expires_at", now) \
        .execute()

    count = len(result.data or [])
    if count:
        logger.info(f"Cleaned up {count} expired invites")
    return count
