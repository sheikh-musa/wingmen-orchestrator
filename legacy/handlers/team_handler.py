"""Team Handler — manage team members via conversation.

Owner can: add [name] as [role], remove [name], list team
"""

from __future__ import annotations

import logging
from ai_provider import call_ai, extract_json
from legacy.invite_manager import create_invite
from legacy.conversation import clear_conversation

logger = logging.getLogger("wingmen.handlers.team")


async def handle(supabase, bot, chat_id: str, message: str, state, user, client_bot) -> str:
    """Process a team management message."""

    # Parse the team action from natural language
    parsed = await call_ai(
        f'Parse this team management request.\n\nUser said: "{message}"\n\n'
        f'Return JSON: {{"action": "add|remove|list", "name": "...", "role": "owner|manager|staff"}}',
        model="fast",
        json_mode=True,
    )
    action_data = extract_json(parsed)

    if not action_data or not isinstance(action_data, dict):
        return "I can help you manage your team. Try:\n- 'Add Fatimah as manager'\n- 'Remove Rizal'\n- 'List team'"

    action = action_data.get("action", "")

    if action == "add":
        name = action_data.get("name", "Team Member")
        role = action_data.get("role", "staff")
        if role not in ("owner", "manager", "staff"):
            role = "staff"

        invite = await create_invite(
            supabase,
            client_id=client_bot.client_id,
            invited_name=name,
            role=role,
            added_by_id=user.id,
            bot_username=client_bot.bot_username,
        )

        return (
            f"Invite created for {name} as {role}.\n\n"
            f"Share this link with them:\n{invite['deep_link']}\n\n"
            f"Expires in 24 hours."
        )

    elif action == "remove":
        name = action_data.get("name", "")
        if not name:
            return "Who would you like to remove?"

        # Find and deactivate
        result = await supabase.table("bot_users") \
            .select("id, name, role") \
            .eq("client_id", client_bot.client_id) \
            .eq("status", "active") \
            .ilike("name", f"%{name}%") \
            .execute()

        matches = result.data or []
        if not matches:
            return f"No team member found matching '{name}'."

        member = matches[0]
        if member["role"] == "owner":
            return "Cannot remove the owner."

        await supabase.table("bot_users").update({
            "status": "deactivated",
            "is_active": False,
        }).eq("id", member["id"]).execute()

        return f"{member['name']} ({member['role']}) has been removed."

    elif action == "list":
        result = await supabase.table("bot_users") \
            .select("name, role, status") \
            .eq("client_id", client_bot.client_id) \
            .neq("role", "customer") \
            .eq("is_active", True) \
            .execute()

        members = result.data or []
        if not members:
            return "No team members yet. Say 'Add [name] as [role]' to invite someone."

        text = "Your team:\n\n"
        for m in members:
            role_label = {"owner": "[Owner]", "manager": "[Manager]", "staff": "[Staff]"}.get(m["role"], "-")
            text += f"{role_label} {m['name']} — {m['role']}\n"
        return text

    return "I can help you manage your team. Try: 'Add Fatimah as manager', 'Remove Rizal', or 'List team'"
