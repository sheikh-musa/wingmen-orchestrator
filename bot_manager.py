"""Bot Manager — registers and routes webhooks for multiple client bots.

On startup: loads all client bot tokens from Supabase, registers webhooks.
On new client: hot-registers new webhook without restart.
Maps incoming webhook requests to client records.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger("wingmen.bot_manager")

WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE_URL", os.environ.get("TUNNEL_BASE_URL", ""))


@dataclass
class ClientBot:
    client_id: int
    token: str
    token_hash: str
    bot_username: str
    bot_display_name: str
    personality: str | None
    welcome_message: str | None
    capabilities: list[str]
    repo_name: str | None
    platform: str | None
    ihsanos_org_id: str | None


def compute_token_hash(token: str) -> str:
    """SHA-256 hash of token, first 16 chars. Used in webhook URL."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class BotManager:
    def __init__(self):
        self._bots: dict[str, ClientBot] = {}  # token_hash -> ClientBot
        self._by_client_id: dict[int, ClientBot] = {}

    @property
    def bots(self) -> dict[str, ClientBot]:
        return self._bots

    def get_by_hash(self, token_hash: str) -> ClientBot | None:
        return self._bots.get(token_hash)

    def get_by_client_id(self, client_id: int) -> ClientBot | None:
        return self._by_client_id.get(client_id)

    async def load_all(self, supabase) -> int:
        """Load all active client bots from Supabase."""
        result = await supabase.table("clients").select("*").eq("active", True).not_.is_("telegram_bot_token", "null").execute()

        count = 0
        for client in (result.data or []):
            token = client["telegram_bot_token"]
            token_hash = compute_token_hash(token)

            bot = ClientBot(
                client_id=client["id"],
                token=token,
                token_hash=token_hash,
                bot_username=client.get("bot_username", ""),
                bot_display_name=client.get("bot_display_name", client.get("name", "")),
                personality=client.get("personality"),
                welcome_message=client.get("welcome_message"),
                capabilities=client.get("capabilities") or [],
                repo_name=client.get("repo_name"),
                platform=client.get("platform"),
                ihsanos_org_id=client.get("ihsanos_org_id"),
            )

            self._bots[token_hash] = bot
            self._by_client_id[client["id"]] = bot
            count += 1

        logger.info(f"Loaded {count} client bots")
        return count

    async def register_bot(self, client_data: dict) -> ClientBot:
        """Register a new client bot (hot-reload, no restart needed)."""
        token = client_data["telegram_bot_token"]
        token_hash = compute_token_hash(token)

        bot = ClientBot(
            client_id=client_data["id"],
            token=token,
            token_hash=token_hash,
            bot_username=client_data.get("bot_username", ""),
            bot_display_name=client_data.get("bot_display_name", client_data.get("name", "")),
            personality=client_data.get("personality"),
            welcome_message=client_data.get("welcome_message"),
            capabilities=client_data.get("capabilities") or [],
            repo_name=client_data.get("repo_name"),
            platform=client_data.get("platform"),
            ihsanos_org_id=client_data.get("ihsanos_org_id"),
        )

        self._bots[token_hash] = bot
        self._by_client_id[client_data["id"]] = bot

        # Register webhook with Telegram
        await self._set_webhook(token, token_hash)

        logger.info(f"Registered bot @{bot.bot_username} ({token_hash})")
        return bot

    async def deactivate_bot(self, client_id: int) -> None:
        """Deactivate a client bot."""
        bot = self._by_client_id.get(client_id)
        if not bot:
            return

        # Delete webhook
        async with httpx.AsyncClient() as client:
            await client.post(f"https://api.telegram.org/bot{bot.token}/deleteWebhook")

        del self._bots[bot.token_hash]
        del self._by_client_id[client_id]
        logger.info(f"Deactivated bot @{bot.bot_username}")

    async def register_all_webhooks(self) -> None:
        """Register webhooks for all loaded bots."""
        for token_hash, bot in self._bots.items():
            await self._set_webhook(bot.token, token_hash)

    async def _set_webhook(self, token: str, token_hash: str) -> bool:
        """Register webhook URL with Telegram."""
        if not WEBHOOK_BASE:
            logger.warning("WEBHOOK_BASE_URL not set, skipping webhook registration")
            return False

        url = f"{WEBHOOK_BASE}/webhook/{token_hash}"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/setWebhook",
                json={"url": url, "allowed_updates": ["message", "callback_query", "my_chat_member"]},
            )
            data = resp.json()
            if data.get("ok"):
                logger.info(f"Webhook set: {url}")
                return True
            else:
                logger.error(f"Webhook registration failed: {data}")
                return False
