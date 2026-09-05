"""Bot User Resolver — identifies who is messaging which bot.

Looks up bot_users by (client_id, telegram_chat_id).
Auto-registers unknown users as customers.
Handles pending invite code claims.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("wingmen.bot_user_resolver")


@dataclass
class BotUser:
    id: int
    client_id: int
    telegram_chat_id: str
    name: str
    role: str  # owner, manager, staff, customer
    permissions: list[str]
    status: str  # pending, active, deactivated
    is_active: bool


_cache: dict[tuple[int, str], BotUser] = {}
_CACHE_TTL = 300  # 5 minutes


async def resolve_user(
    supabase,
    client_id: int,
    telegram_chat_id: str,
    telegram_username: str | None = None,
    display_name: str = "Customer",
) -> BotUser:
    """Look up or auto-register a bot user."""

    cache_key = (client_id, telegram_chat_id)
    if cache_key in _cache:
        return _cache[cache_key]

    # Look up in DB
    result = await supabase.table("bot_users") \
        .select("*") \
        .eq("client_id", client_id) \
        .eq("telegram_chat_id", telegram_chat_id) \
        .maybeSingle() \
        .execute()

    if result.data:
        user = BotUser(
            id=result.data["id"],
            client_id=result.data["client_id"],
            telegram_chat_id=result.data["telegram_chat_id"],
            name=result.data["name"],
            role=result.data["role"],
            permissions=result.data.get("permissions") or [],
            status=result.data["status"],
            is_active=result.data["is_active"],
        )
        _cache[cache_key] = user
        return user

    # Auto-register as customer
    insert_result = await supabase.table("bot_users").insert({
        "client_id": client_id,
        "telegram_chat_id": telegram_chat_id,
        "telegram_username": telegram_username,
        "name": display_name,
        "role": "customer",
        "status": "active",
    }).execute()

    user = BotUser(
        id=insert_result.data[0]["id"],
        client_id=client_id,
        telegram_chat_id=telegram_chat_id,
        name=display_name,
        role="customer",
        permissions=[],
        status="active",
        is_active=True,
    )
    _cache[cache_key] = user
    logger.info(f"Auto-registered customer: {display_name} for client {client_id}")
    return user


async def try_claim_invite(
    supabase,
    client_id: int,
    invite_code: str,
    telegram_chat_id: str,
    telegram_username: str | None,
    display_name: str,
) -> BotUser | None:
    """Try to claim an invite code. Returns activated user or None."""

    result = await supabase.table("bot_users") \
        .select("*") \
        .eq("client_id", client_id) \
        .eq("invite_code", invite_code) \
        .eq("status", "pending") \
        .maybeSingle() \
        .execute()

    if not result.data:
        return None

    # Check expiry
    expires = result.data.get("invite_expires_at")
    if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
        return None

    # Activate
    update_result = await supabase.table("bot_users").update({
        "telegram_chat_id": telegram_chat_id,
        "telegram_username": telegram_username,
        "name": display_name,
        "status": "active",
        "invite_code": None,  # single-use
    }).eq("id", result.data["id"]).execute()

    user = BotUser(
        id=result.data["id"],
        client_id=client_id,
        telegram_chat_id=telegram_chat_id,
        name=display_name,
        role=result.data["role"],
        permissions=result.data.get("permissions") or [],
        status="active",
        is_active=True,
    )

    # Clear cache for this user
    _cache[(client_id, telegram_chat_id)] = user
    logger.info(f"Invite claimed: {display_name} as {result.data['role']} for client {client_id}")
    return user


def clear_cache(client_id: int = None, telegram_chat_id: str = None):
    """Clear user cache."""
    if client_id and telegram_chat_id:
        _cache.pop((client_id, telegram_chat_id), None)
    else:
        _cache.clear()
