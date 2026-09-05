"""Group Setup — links client bots to Telegram groups and configures scoped commands."""

from __future__ import annotations

import logging

import httpx
from telegram import Bot

from legacy.bot_onboarding import get_default_commands

logger = logging.getLogger("wingmen.group_setup")


async def link_group(
    supabase,
    client_bot,
    group_chat_id: str,
    group_name: str | None,
    group_type: str,
) -> bool:
    """Upsert a client_groups row, set scoped commands, and send a welcome message."""
    try:
        await supabase.table("client_groups").upsert(
            {
                "client_id": client_bot.client_id,
                "group_chat_id": group_chat_id,
                "group_name": group_name,
                "group_type": group_type,
                "is_active": True,
            },
            on_conflict="client_id,group_chat_id",
        ).execute()

        await set_scoped_commands(client_bot.token, group_chat_id, client_bot.capabilities)

        welcome = build_group_welcome(client_bot.bot_display_name, client_bot.capabilities)
        bot = Bot(token=client_bot.token)
        await bot.send_message(chat_id=group_chat_id, text=welcome)

        logger.info(f"Linked group '{group_name}' ({group_chat_id}) to client {client_bot.client_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to link group {group_chat_id}: {e}", exc_info=True)
        return False


async def set_scoped_commands(token: str, group_chat_id: str, capabilities: list[str]) -> None:
    """Set bot commands scoped to a specific group chat."""
    commands = get_default_commands(capabilities)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/setMyCommands",
            json={
                "commands": commands,
                "scope": {"type": "chat", "chat_id": group_chat_id},
            },
        )
        data = resp.json()
        if not data.get("ok"):
            logger.warning(f"setMyCommands failed for group {group_chat_id}: {data}")


def build_group_welcome(bot_display_name: str, capabilities: list[str]) -> str:
    """Build a templated welcome message listing available commands."""
    commands = get_default_commands(capabilities)
    lines = [f"Assalamu alaikum! I'm {bot_display_name}.\n\nAvailable commands:"]
    for cmd in commands:
        lines.append(f"  /{cmd['command']} — {cmd['description']}")
    lines.append("\nType /help for more info.")
    return "\n".join(lines)
