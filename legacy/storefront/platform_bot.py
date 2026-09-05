"""Load the single shared platform bot (@dookanabot) into the BotManager.

Unlike per-merchant bots, the platform bot has NO fixed ihsanos_org_id — the
merchant is resolved per-conversation from the ?start=<slug> deep-link. It is
registered with is_platform=True so the dispatcher takes the shared-bot path.
"""
from __future__ import annotations

import logging
import os

from legacy.bot_manager import BotManager, ClientBot, compute_token_hash
from legacy.bot_onboarding import validate_token

logger = logging.getLogger("wingmen.storefront.platform_bot")

_PLATFORM_CLIENT_ID = -1  # sentinel: the platform bot is not a merchant client


async def load_platform_bot(bot_manager: BotManager) -> ClientBot | None:
    """Read STOREFRONT_TG_BOT_TOKEN, validate via getMe, register the bot.

    Returns the registered ClientBot, or None if the token is unset/invalid.
    """
    token = os.environ.get("STOREFRONT_TG_BOT_TOKEN")
    if not token:
        logger.warning("STOREFRONT_TG_BOT_TOKEN not set; platform bot not loaded")
        return None

    info = await validate_token(token)
    if not info:
        logger.error("STOREFRONT_TG_BOT_TOKEN failed getMe validation")
        return None

    bot = ClientBot(
        client_id=_PLATFORM_CLIENT_ID,
        token=token,
        token_hash=compute_token_hash(token),
        bot_username=info.get("username", ""),
        bot_display_name=info.get("first_name", "Storefront"),
        personality=None,
        welcome_message=None,
        capabilities=["storefront"],
        repo_name=None,
        platform="ihsanos",
        ihsanos_org_id=None,
        is_platform=True,
        slug=None,
    )

    bot_manager._bots[bot.token_hash] = bot
    bot_manager._by_client_id[bot.client_id] = bot
    await bot_manager._set_webhook(bot.token, bot.token_hash)
    logger.info(f"Loaded platform bot @{bot.bot_username}")
    return bot
