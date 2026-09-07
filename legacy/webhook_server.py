"""Webhook Server — receives Telegram updates for all client bots.

Runs as a separate async server alongside the main orchestrator.
Single endpoint: POST /webhook/{token_hash}
"""

from __future__ import annotations

import json
import logging

from aiohttp import web

from legacy.bot_manager import BotManager

logger = logging.getLogger("wingmen.webhook_server")


async def create_webhook_app(bot_manager: BotManager, message_handler) -> web.Application:
    """Create the aiohttp app for webhook handling.

    Args:
        bot_manager: manages bot tokens and client mapping
        message_handler: async callable(client_bot, update_data) that processes the message
    """
    app = web.Application()
    app["bot_manager"] = bot_manager
    app["message_handler"] = message_handler

    app.router.add_post("/webhook/{token_hash}", handle_webhook)
    app.router.add_get("/health", handle_health)

    return app


async def handle_webhook(request: web.Request) -> web.Response:
    """Handle incoming Telegram webhook."""
    token_hash = request.match_info["token_hash"]
    bot_manager: BotManager = request.app["bot_manager"]
    message_handler = request.app["message_handler"]

    client_bot = bot_manager.get_by_hash(token_hash)
    if not client_bot:
        return web.Response(status=404, text="Unknown bot")

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")

    # Process in background (don't block Telegram's webhook)
    import asyncio
    asyncio.create_task(message_handler(client_bot, body))

    # Always return 200 quickly (Telegram retries on non-200)
    return web.Response(status=200, text="ok")


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    bot_manager: BotManager = request.app["bot_manager"]
    return web.json_response({
        "status": "healthy",
        "active_bots": len(bot_manager.bots),
    })
