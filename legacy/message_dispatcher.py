"""Message Dispatcher — routes incoming bot messages through the full pipeline.

webhook_server -> message_dispatcher -> user resolver -> conversation -> permission -> handler -> response
"""

from __future__ import annotations

import logging
import os
import re

from telegram import Bot

from ai_provider import call_ai, extract_json
from agents.router import build_router_prompt, parse_router_response
from bot_manager import ClientBot
from bot_user_resolver import resolve_user, try_claim_invite, BotUser
from group_setup import link_group
from conversation import get_conversation, start_conversation, clear_conversation
from permissions import can_do
from personality import build_system_prompt
from heartbeat import write_client_bot_heartbeat

import handlers.order_handler as order_handler
import handlers.qurban_handler as qurban_handler
import handlers.site_edit_handler as site_edit_handler
import handlers.team_handler as team_handler
import handlers.help_handler as help_handler

from storefront.miniapp import build_miniapp_keyboard

logger = logging.getLogger("wingmen.dispatcher")

# Intent -> handler mapping
FLOW_HANDLERS = {
    "place_order": order_handler,
    "book_qurban": qurban_handler,
    "site_edit": site_edit_handler,
    "manage_team": team_handler,
    "help": help_handler,
}

# Flow name -> intent mapping (for conversation resumption)
FLOW_TO_INTENT = {
    "ordering": "place_order",
    "qurban_booking": "book_qurban",
    "site_edit": "site_edit",
    "team_manage": "manage_team",
}

_STOREFRONT_WEB_BASE_DEFAULT = "https://ihsanos.com"


async def _resolve_storefront_slug(supabase, slug: str) -> dict | None:
    """Look up a merchant clients row by storefront_slug. Read-only."""
    result = (
        await supabase.table("clients")
        .select("id, name, storefront_slug, ihsanos_org_id, welcome_message")
        .eq("storefront_slug", slug)
        .eq("active", True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


async def _handle_storefront_start(bot, supabase, chat_id: str, slug: str) -> None:
    """Shared-bot /start <slug>: resolve slug, send welcome + Mini App button.

    Unknown slug -> graceful fallback. No state persisted; the slug travels in
    the web_app button URL.
    """
    row = await _resolve_storefront_slug(supabase, slug)
    if not row:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "That shop link doesn't look right. Please use the link the "
                "shop shared with you, or ask them for a new one."
            ),
        )
        return

    web_base = os.environ.get("STOREFRONT_WEB_BASE", _STOREFRONT_WEB_BASE_DEFAULT)
    keyboard = build_miniapp_keyboard(row["storefront_slug"], web_base)
    welcome = row.get("welcome_message") or f"Welcome to {row['name']}!"
    await bot.send_message(
        chat_id=chat_id,
        text=f"{welcome}\n\nTap below to start shopping \U0001f447",
        reply_markup=keyboard,
    )


async def dispatch(client_bot: ClientBot, update_data: dict, supabase) -> None:
    """Process an incoming Telegram update for a client bot.

    This is the function passed to webhook_server as the message_handler callback.
    """
    try:
        # --- Handle my_chat_member updates (bot added/removed from groups) ---
        chat_member = update_data.get("my_chat_member")
        if chat_member:
            await _handle_chat_member(client_bot, chat_member, supabase)
            return

        # Parse the Telegram update
        message = update_data.get("message") or update_data.get("callback_query", {}).get("message")
        callback_query = update_data.get("callback_query")

        if not message:
            return

        chat_id = str(message.get("chat", {}).get("id", ""))
        chat_type = message.get("chat", {}).get("type", "private")
        from_user = message.get("from", {})
        text = message.get("text", "").strip()

        if not chat_id or not text:
            return

        # --- Group message filtering ---
        is_group = chat_type in ("group", "supergroup")
        if is_group:
            if not await _should_handle_group_message(supabase, client_bot, chat_id, text, message):
                return

        # In groups, resolve users by their personal ID, not the group chat ID
        user_chat_id = str(from_user.get("id", "")) if is_group else chat_id
        reply_to = message.get("message_id") if is_group else None

        # Strip @bot_username from commands in groups
        if is_group and text.startswith("/") and "@" in text.split()[0]:
            text = text.split()[0].split("@")[0] + " " + " ".join(text.split()[1:])
            text = text.strip()

        # Create a Bot instance for sending responses
        bot = Bot(token=client_bot.token)

        # --- Shared platform bot: /start <slug> opens the merchant Mini App ---
        if client_bot.is_platform and text.startswith("/start "):
            slug = text.split(" ", 1)[1].strip()
            if slug.startswith("invite_"):
                await _handle_invite(
                    supabase, bot, client_bot, chat_id, from_user,
                    slug.split("invite_", 1)[1].strip(),
                )
            else:
                await _handle_storefront_start(bot, supabase, chat_id, slug)
            return

        # --- Handle /start with invite code ---
        if text.startswith("/start invite_"):
            invite_code = text.split("invite_", 1)[1].strip()
            await _handle_invite(supabase, bot, client_bot, chat_id, from_user, invite_code)
            return

        # --- Handle /start (welcome message) ---
        if text == "/start":
            if client_bot.is_platform:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "Welcome! Open a shop using the link your merchant "
                        "shared with you."
                    ),
                    reply_to_message_id=reply_to,
                )
                return
            welcome = client_bot.welcome_message or f"Welcome to {client_bot.bot_display_name}! How can I help you?"
            await bot.send_message(chat_id=chat_id, text=welcome, reply_to_message_id=reply_to)
            # Auto-register as customer
            await resolve_user(
                supabase, client_bot.client_id, user_chat_id,
                telegram_username=from_user.get("username"),
                display_name=from_user.get("first_name", "Customer"),
            )
            return

        # MVP: the shared platform bot only launches the Mini App. Conversational
        # ordering happens inside the Mini App, not in chat. Anything that isn't a
        # recognized /start lands here. Chat->org binding is a follow-up
        # (CC-ORCH-STOREFRONT-CHAT-BINDING-001).
        if client_bot.is_platform:
            await bot.send_message(
                chat_id=chat_id,
                text="Open your shop with its link to browse and order \U0001f6cd️",
                reply_to_message_id=reply_to,
            )
            return

        # --- Resolve user ---
        user = await resolve_user(
            supabase, client_bot.client_id, user_chat_id,
            telegram_username=from_user.get("username"),
            display_name=from_user.get("first_name", "Customer"),
        )

        # --- Check for active conversation ---
        active_conv = await get_conversation(supabase, client_bot.client_id, user_chat_id)

        # If user says "cancel" or "stop", clear the conversation
        if active_conv and text.lower() in ("cancel", "stop", "quit", "exit", "/cancel"):
            await clear_conversation(supabase, client_bot.client_id, user_chat_id)
            await bot.send_message(chat_id=chat_id, text="Cancelled. How else can I help?", reply_to_message_id=reply_to)
            return

        # If active conversation exists, resume it
        if active_conv:
            intent_for_flow = FLOW_TO_INTENT.get(active_conv.flow)
            if intent_for_flow and intent_for_flow in FLOW_HANDLERS:
                handler = FLOW_HANDLERS[intent_for_flow]
                response = await handler.handle(supabase, bot, chat_id, text, active_conv, user, client_bot)
                await bot.send_message(chat_id=chat_id, text=response, reply_to_message_id=reply_to)
                await _write_heartbeat(supabase, client_bot)
                return

        # --- Route intent ---
        # Handle command shortcuts
        intent = _command_to_intent(text)

        if not intent:
            # Use AI router
            router_prompt = build_router_prompt(
                text,
                repos=[client_bot.repo_name or "unknown"],
                history=[],
                role="client",
            )
            router_response = await call_ai(router_prompt, model="fast", json_mode=True)
            parsed = parse_router_response(router_response)
            intent = parsed.get("intent", "chat")

        # --- Permission check ---
        if not can_do(user.role, client_bot.capabilities, intent):
            # Downgrade to chat if not permitted
            if intent in ("site_edit", "manage_team", "bug_report", "analytics"):
                await bot.send_message(
                    chat_id=chat_id,
                    text="Sorry, you don't have permission for that. Type /help to see what you can do.",
                    reply_to_message_id=reply_to,
                )
                return
            intent = "chat"

        # --- Handle bug report ---
        if intent == "bug_report":
            from bug_pipeline import create_bug_report
            await create_bug_report(
                supabase,
                client_id=client_bot.client_id,
                reporter_name=user.name,
                reporter_email=None,
                reporter_source="telegram",
                auth_provider="telegram",
                repo_name=client_bot.repo_name or "unknown",
                description=text,
            )
            await bot.send_message(chat_id=chat_id, text="Got it -- I'm diagnosing this now. I'll notify you when there's a fix.", reply_to_message_id=reply_to)
            await _write_heartbeat(supabase, client_bot)
            return

        # --- Handle track_order ---
        if intent == "track_order":
            response = await _handle_track_order(supabase, bot, chat_id, text, user, client_bot)
            await bot.send_message(chat_id=chat_id, text=response, reply_to_message_id=reply_to)
            await _write_heartbeat(supabase, client_bot)
            return

        # --- Handle flow-based intents ---
        if intent in FLOW_HANDLERS:
            handler = FLOW_HANDLERS[intent]
            response = await handler.handle(supabase, bot, chat_id, text, None, user, client_bot)
            await bot.send_message(chat_id=chat_id, text=response, reply_to_message_id=reply_to)
            await _write_heartbeat(supabase, client_bot)
            return

        # --- Default: chat ---
        system_prompt = build_system_prompt(client_bot, user)
        response = await call_ai(text, system=system_prompt, model="auto")
        await bot.send_message(chat_id=chat_id, text=response, reply_to_message_id=reply_to)
        await _write_heartbeat(supabase, client_bot)

    except Exception as e:
        logger.error(f"Dispatch error for bot @{client_bot.bot_username}: {e}", exc_info=True)
        try:
            bot = Bot(token=client_bot.token)
            await bot.send_message(
                chat_id=str(update_data.get("message", {}).get("chat", {}).get("id", "")),
                text="Sorry, something went wrong. Please try again.",
            )
        except Exception:
            pass


async def _handle_invite(supabase, bot: Bot, client_bot: ClientBot, chat_id: str, from_user: dict, invite_code: str) -> None:
    """Handle /start invite_CODE deep link."""
    display_name = from_user.get("first_name", "Team Member")
    username = from_user.get("username")

    user = await try_claim_invite(
        supabase, client_bot.client_id, invite_code,
        telegram_chat_id=chat_id,
        telegram_username=username,
        display_name=display_name,
    )

    if user:
        role_label = {"owner": "[Owner]", "manager": "[Manager]", "staff": "[Staff]"}.get(user.role, "[Member]")
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"{role_label} Welcome {display_name}! You've been added as {user.role} "
                f"at {client_bot.bot_display_name}.\n\nType /help to see what you can do."
            ),
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text="This invite link is invalid or has expired. Please ask the owner for a new one.",
        )


async def _handle_track_order(supabase, bot: Bot, chat_id: str, text: str, user: BotUser, client_bot: ClientBot) -> str:
    """Handle order tracking requests."""
    # Try to extract order number from message
    match = re.search(r"ORD-\d{4}-\d+", text, re.IGNORECASE)

    if match:
        order_ref = match.group(0).upper()
        return f"To track order {order_ref}, visit:\nhttps://ihsanos.com/shop/{client_bot.bot_username}/orders/{order_ref}"

    # No order number -- ask for it
    return "Please provide your order number (e.g., ORD-2026-042) or the email you used to order."


def _command_to_intent(text: str) -> str | None:
    """Map Telegram commands to intents."""
    commands = {
        "/order": "place_order",
        "/menu": "place_order",
        "/track": "track_order",
        "/qurban": "book_qurban",
        "/bug": "bug_report",
        "/team": "manage_team",
        "/help": "help",
        "/edit": "site_edit",
    }
    cmd = text.split()[0].lower() if text.startswith("/") else None
    return commands.get(cmd)


async def _handle_chat_member(client_bot: ClientBot, chat_member: dict, supabase) -> None:
    """Handle my_chat_member updates — bot added to or removed from a group."""
    chat = chat_member.get("chat", {})
    chat_type = chat.get("type", "")
    if chat_type not in ("group", "supergroup"):
        return

    new_status = chat_member.get("new_chat_member", {}).get("status", "")
    group_chat_id = str(chat.get("id", ""))
    group_name = chat.get("title")

    if new_status in ("member", "administrator", "restricted"):
        await link_group(supabase, client_bot, group_chat_id, group_name, chat_type)
    elif new_status in ("left", "kicked"):
        try:
            await supabase.table("client_groups").update({"is_active": False}).eq(
                "client_id", client_bot.client_id
            ).eq("group_chat_id", group_chat_id).execute()
            logger.info(f"Deactivated group {group_chat_id} for client {client_bot.client_id}")
        except Exception as e:
            logger.error(f"Failed to deactivate group {group_chat_id}: {e}")


async def _should_handle_group_message(supabase, client_bot: ClientBot, group_chat_id: str, text: str, message: dict) -> bool:
    """Return True if the bot should respond to this group message."""
    # Verify group is linked
    result = await supabase.table("client_groups").select("id").eq(
        "client_id", client_bot.client_id
    ).eq("group_chat_id", group_chat_id).eq("is_active", True).limit(1).execute()
    if not result.data:
        return False

    # In linked groups, only respond to commands, @mentions, and replies to the bot
    if text.startswith("/"):
        return True

    bot_username = client_bot.bot_username
    if bot_username and f"@{bot_username}" in text:
        return True

    reply = message.get("reply_to_message", {})
    reply_from = reply.get("from", {})
    if reply_from.get("is_bot") and reply_from.get("username") == bot_username:
        return True

    return False


async def _write_heartbeat(supabase, client_bot: ClientBot) -> None:
    """Write heartbeat for this bot (non-blocking)."""
    try:
        await write_client_bot_heartbeat(supabase, client_bot.client_id, client_bot.bot_username)
    except Exception:
        pass
