"""Bot Onboarding — validates new client bot tokens and configures them.

Flow: client pastes token → validate via getMe → configure (name, desc, commands) → register webhook → create owner
"""

from __future__ import annotations

import logging
import httpx

from legacy.bot_manager import BotManager, compute_token_hash

logger = logging.getLogger("wingmen.bot_onboarding")

TELEGRAM_API = "https://api.telegram.org/bot{token}"


async def validate_token(token: str) -> dict | None:
    """Validate bot token via Telegram getMe API. Returns bot info or None."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{TELEGRAM_API.format(token=token)}/getMe")
            data = resp.json()
            if data.get("ok"):
                return data["result"]  # {id, is_bot, first_name, username, ...}
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
    return None


async def configure_bot(
    token: str,
    display_name: str,
    description: str,
    short_description: str,
    commands: list[dict],  # [{"command": "start", "description": "Get started"}]
) -> bool:
    """Configure bot via Telegram Bot API: name, description, commands."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            base = TELEGRAM_API.format(token=token)

            # Set display name
            await client.post(f"{base}/setMyName", json={"name": display_name[:64]})

            # Set description (shown before user starts the bot)
            await client.post(f"{base}/setMyDescription", json={"description": description[:512]})

            # Set short description (shown in bot profile)
            await client.post(f"{base}/setMyShortDescription", json={"short_description": short_description[:120]})

            # Set command menu
            if commands:
                await client.post(f"{base}/setMyCommands", json={"commands": commands[:10]})

            return True
    except Exception as e:
        logger.error(f"Bot configuration failed: {e}")
        return False


def get_default_commands(capabilities: list[str]) -> list[dict]:
    """Generate bot command menu based on capabilities."""
    commands = [
        {"command": "start", "description": "Get started"},
        {"command": "help", "description": "What can I do?"},
    ]

    if "storefront" in capabilities:
        commands.append({"command": "menu", "description": "Browse our menu"})
        commands.append({"command": "order", "description": "Place an order"})

    if "qurban" in capabilities:
        commands.append({"command": "qurban", "description": "Book qurban"})

    if "bug_report" in capabilities:
        commands.append({"command": "bug", "description": "Report an issue"})

    return commands


async def onboard_client_bot(
    supabase,
    bot_manager: BotManager,
    client_id: int,
    token: str,
    display_name: str,
    personality: str | None = None,
    welcome_message: str | None = None,
    capabilities: list[str] | None = None,
) -> dict | None:
    """Full onboarding: validate → configure → register webhook → update client → create owner.

    Returns the bot info dict or None on failure.
    """
    # Step 1: Validate
    bot_info = await validate_token(token)
    if not bot_info:
        return None

    bot_username = bot_info.get("username", "")
    caps = capabilities or ["support", "bug_report"]

    # Step 2: Configure
    description = f"{display_name} — powered by Wingmen"
    if personality:
        description = personality[:200]

    commands = get_default_commands(caps)
    await configure_bot(token, display_name, description, f"{display_name} assistant", commands)

    # Step 3: Update client record
    await supabase.table("clients").update({
        "telegram_bot_token": token,
        "bot_username": bot_username,
        "bot_display_name": display_name,
        "personality": personality,
        "welcome_message": welcome_message or f"Welcome to {display_name}! How can I help you?",
        "capabilities": caps,
    }).eq("id", client_id).execute()

    # Step 4: Register webhook + add to bot manager
    client_data = (await supabase.table("clients").select("*").eq("id", client_id).single().execute()).data
    await bot_manager.register_bot(client_data)

    logger.info(f"Onboarded @{bot_username} for client {client_id}")
    return bot_info
