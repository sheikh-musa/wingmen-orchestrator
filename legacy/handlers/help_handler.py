"""Help Handler — context-aware help responses.

Reads storefront config and provides role-appropriate guidance.
"""

from __future__ import annotations

import logging
from ai_provider import call_ai

logger = logging.getLogger("wingmen.handlers.help")


async def handle(supabase, bot, chat_id: str, message: str, state, user, client_bot) -> str:
    """Provide context-aware help."""

    role = user.role
    capabilities = client_bot.capabilities

    # Build context about what the user can do
    role_help = {
        "owner": "You're the owner. You can: edit your website, view orders, manage your team, report bugs, view analytics.",
        "manager": "You're a manager. You can: view and manage orders, update the menu.",
        "staff": "You're staff. You can: view orders and mark them as ready.",
        "customer": "You're a customer. You can: browse the menu, place orders, track your orders.",
    }

    context = role_help.get(role, "How can I help?")

    if "storefront" in capabilities:
        context += " This business has an online store."
    if "qurban" in capabilities:
        context += " Qurban bookings are available."

    response = await call_ai(
        f"The user asked for help: \"{message}\"\n\nContext: {context}\nBusiness: {client_bot.bot_display_name}\n\nGive a helpful, concise response. If they asked a specific question, answer it. If general, list what they can do.",
        system=client_bot.personality or f"You are a helpful assistant for {client_bot.bot_display_name}.",
        model="fast",
    )

    return response
