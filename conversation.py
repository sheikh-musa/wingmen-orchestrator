"""Conversation State Machine — manages multi-turn flows per user per bot.

Persists state in bot_conversations table with in-memory cache.
Each user can have one active conversation at a time per bot.
Conversations expire after 1 hour of inactivity.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger("wingmen.conversation")

CONVERSATION_TTL_HOURS = 1


@dataclass
class ConversationState:
    client_id: int
    telegram_chat_id: str
    flow: str           # ordering, qurban_booking, site_edit, team_manage
    step: str           # current step in the flow
    data: dict = field(default_factory=dict)  # accumulated state
    db_id: int | None = None


_cache: dict[tuple[int, str], ConversationState] = {}


async def get_conversation(supabase, client_id: int, chat_id: str) -> ConversationState | None:
    """Get active conversation for a user, checking cache then DB."""
    cache_key = (client_id, chat_id)
    if cache_key in _cache:
        return _cache[cache_key]

    result = await supabase.table("bot_conversations") \
        .select("*") \
        .eq("client_id", client_id) \
        .eq("telegram_chat_id", chat_id) \
        .maybeSingle() \
        .execute()

    if not result.data:
        return None

    # Check expiry
    expires = result.data.get("expires_at")
    if expires:
        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if exp_dt < datetime.now(timezone.utc):
            await clear_conversation(supabase, client_id, chat_id)
            return None

    state = ConversationState(
        client_id=client_id,
        telegram_chat_id=chat_id,
        flow=result.data["flow"],
        step=result.data["step"],
        data=result.data.get("state_data") or {},
        db_id=result.data["id"],
    )
    _cache[cache_key] = state
    return state


async def start_conversation(
    supabase, client_id: int, chat_id: str, flow: str, step: str, data: dict | None = None,
) -> ConversationState:
    """Start a new conversation flow (clears any existing one)."""
    await clear_conversation(supabase, client_id, chat_id)

    expires = (datetime.now(timezone.utc) + timedelta(hours=CONVERSATION_TTL_HOURS)).isoformat()

    result = await supabase.table("bot_conversations").insert({
        "client_id": client_id,
        "telegram_chat_id": chat_id,
        "flow": flow,
        "step": step,
        "state_data": data or {},
        "expires_at": expires,
    }).execute()

    state = ConversationState(
        client_id=client_id,
        telegram_chat_id=chat_id,
        flow=flow,
        step=step,
        data=data or {},
        db_id=result.data[0]["id"],
    )
    _cache[(client_id, chat_id)] = state
    return state


async def update_conversation(
    supabase, state: ConversationState, step: str, data_updates: dict | None = None,
) -> ConversationState:
    """Update conversation step and data."""
    state.step = step
    if data_updates:
        state.data.update(data_updates)

    expires = (datetime.now(timezone.utc) + timedelta(hours=CONVERSATION_TTL_HOURS)).isoformat()

    await supabase.table("bot_conversations").update({
        "step": step,
        "state_data": state.data,
        "expires_at": expires,
    }).eq("client_id", state.client_id).eq("telegram_chat_id", state.telegram_chat_id).execute()

    _cache[(state.client_id, state.telegram_chat_id)] = state
    return state


async def clear_conversation(supabase, client_id: int, chat_id: str) -> None:
    """Clear active conversation."""
    await supabase.table("bot_conversations") \
        .delete() \
        .eq("client_id", client_id) \
        .eq("telegram_chat_id", chat_id) \
        .execute()
    _cache.pop((client_id, chat_id), None)
