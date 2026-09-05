"""Personality Builder — constructs system prompts for client bots.

Combines client personality, user role, and capability context into
a coherent system prompt for call_ai().
"""

from __future__ import annotations

from legacy.bot_manager import ClientBot
from legacy.bot_user_resolver import BotUser


def build_system_prompt(client_bot: ClientBot, user: BotUser) -> str:
    """Build a system prompt tailored to this bot + user combination."""

    # Base personality
    personality = client_bot.personality or f"You are a helpful assistant for {client_bot.bot_display_name}."

    # Role context
    role_context = {
        "owner": f"This user is the owner of {client_bot.bot_display_name}. They have full access to all features including site editing, team management, and analytics.",
        "manager": f"This user is a manager at {client_bot.bot_display_name}. They can view orders and update the menu but cannot edit the site or manage the team.",
        "staff": f"This user is staff at {client_bot.bot_display_name}. They can view orders assigned to them.",
        "customer": f"This user is a customer. Help them with ordering, tracking, and general questions about {client_bot.bot_display_name}.",
    }

    # Capability context
    cap_lines = []
    if "storefront" in client_bot.capabilities:
        cap_lines.append("This business has an online store with products/menu.")
    if "qurban" in client_bot.capabilities:
        cap_lines.append("Qurban (animal sacrifice) bookings are available.")
    if "bug_report" in client_bot.capabilities:
        cap_lines.append("The owner can report technical issues.")

    parts = [
        personality,
        "",
        role_context.get(user.role, ""),
        "",
        "\n".join(cap_lines) if cap_lines else "",
        "",
        "Keep responses concise and friendly. Use emojis sparingly. If unsure, ask for clarification rather than guessing.",
    ]

    return "\n".join(parts).strip()
