"""Site Edit Handler — conversational storefront editing.

Fetches current StorefrontConfig, AI identifies what to change, updates via API.
"""

from __future__ import annotations

import logging
import httpx
from ai_provider import call_ai, extract_json
from conversation import ConversationState, start_conversation, update_conversation, clear_conversation

logger = logging.getLogger("wingmen.handlers.site_edit")

IHSANOS_API = "https://ihsanos.com/api"


async def handle(supabase, bot, chat_id: str, message: str, state: ConversationState | None, user, client_bot) -> str:
    """Process a site edit message."""

    if state is None or state.flow != "site_edit":
        # Fetch current config
        config = await _fetch_config(client_bot.ihsanos_org_id)
        state = await start_conversation(supabase, client_bot.client_id, chat_id, "site_edit", "describe_change", {"config": config})

    step = state.step

    if step == "describe_change":
        config = state.data.get("config", {})

        # AI figures out what to change
        parsed = await call_ai(
            f'The user wants to edit their storefront. Current config:\n{str(config)[:2000]}\n\n'
            f'User said: "{message}"\n\n'
            f'Return JSON: {{"field_path": "hero.headline", "new_value": "...", "description": "what will change"}}.\n'
            f'Common paths: hero.headline, hero.subheadline, hero.cta_text, about.text, contact.phone, contact.whatsapp, contact.hours, theme.primary_color',
            model="auto",
            json_mode=True,
        )
        change = extract_json(parsed)

        if not change or not isinstance(change, dict) or not change.get("field_path"):
            return "I'm not sure what you'd like to change. Could you be more specific? For example: 'Change the hero headline to Welcome to our shop'"

        state = await update_conversation(supabase, state, "confirm_change", {"change": change})
        return f"I'll update {change['field_path']} to:\n\n\"{change['new_value']}\"\n\nConfirm? (yes/no)"

    elif step == "confirm_change":
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["yes", "confirm", "ok", "do it"]):
            change = state.data.get("change", {})
            success = await _apply_change(client_bot.ihsanos_org_id, change)
            await clear_conversation(supabase, client_bot.client_id, chat_id)

            if success:
                slug = client_bot.bot_username.replace("_bot", "").replace("_", "-")
                return f"Updated! Preview: ihsanos.com/shop/{slug}\n\nAnything else to change?"
            return "Failed to apply the change. Please try again."
        else:
            await clear_conversation(supabase, client_bot.client_id, chat_id)
            return "Change cancelled. Tell me what you'd like to change whenever you're ready."

    return "Tell me what you'd like to change on your website."


async def _fetch_config(org_id: str | None) -> dict:
    if not org_id:
        return {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{IHSANOS_API}/storefront-config/{org_id}")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch config: {e}")
    return {}


async def _apply_change(org_id: str | None, change: dict) -> bool:
    if not org_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{IHSANOS_API}/storefront-update", json={
                "org_id": org_id,
                "field_path": change["field_path"],
                "new_value": change["new_value"],
            })
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Failed to apply change: {e}")
    return False
