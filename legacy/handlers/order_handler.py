"""Order Handler — conversational ordering flow.

Steps: show_menu -> collect_items -> fulfillment -> address -> confirm -> placed
"""

from __future__ import annotations

import logging
import httpx
from ai_provider import call_ai, extract_json
from conversation import ConversationState, start_conversation, update_conversation, clear_conversation

logger = logging.getLogger("wingmen.handlers.order")

IHSANOS_API = "https://ihsanos.com/api"


async def handle(supabase, bot, chat_id: str, message: str, state: ConversationState | None, user, client_bot) -> str:
    """Process an ordering message. Returns response text."""

    if state is None or state.flow != "ordering":
        # Start new ordering flow
        state = await start_conversation(supabase, client_bot.client_id, chat_id, "ordering", "show_menu")

    step = state.step

    if step == "show_menu":
        # Fetch products
        products = await _fetch_products(client_bot.ihsanos_org_id)
        if not products:
            await clear_conversation(supabase, client_bot.client_id, chat_id)
            return "Sorry, we don't have any items available right now."

        state = await update_conversation(supabase, state, "collect_items", {"products": products})

        menu_text = "Here's our menu:\n\n"
        for p in products:
            menu_text += f"• {p['name']} — ${p['price']:.2f}\n"
        menu_text += "\nWhat would you like to order?"
        return menu_text

    elif step == "collect_items":
        # Use AI to parse natural language into items
        products = state.data.get("products", [])
        product_list = "\n".join(f"- {p['name']} (${p['price']:.2f}, id: {p['id']})" for p in products)

        parsed = await call_ai(
            f"Parse this order into a JSON array of items.\n\nAvailable products:\n{product_list}\n\nCustomer said: \"{message}\"\n\nReturn JSON: [{{\"product_id\": \"...\", \"name\": \"...\", \"quantity\": N, \"price\": N.NN}}]",
            model="fast",
            json_mode=True,
        )
        items = extract_json(parsed)

        if not items or not isinstance(items, list) or len(items) == 0:
            return "I didn't catch that. Could you tell me what you'd like to order? For example: '2 Nasi Briyani and 1 Lamb Mandi'"

        total = sum(i.get("price", 0) * i.get("quantity", 1) for i in items)

        summary = "Your order:\n"
        for i in items:
            summary += f"  {i['quantity']}x {i['name']} — ${i['price'] * i['quantity']:.2f}\n"
        summary += f"\nTotal: ${total:.2f}\n\nPickup or delivery?"

        state = await update_conversation(supabase, state, "fulfillment", {"items": items, "total": total})
        return summary

    elif step == "fulfillment":
        msg_lower = message.lower()
        if "deliver" in msg_lower:
            state = await update_conversation(supabase, state, "address", {"fulfillment": "delivery"})
            return "What's your delivery address?"
        elif "pickup" in msg_lower or "pick up" in msg_lower or "collect" in msg_lower:
            state = await update_conversation(supabase, state, "confirm", {"fulfillment": "pickup"})
            return _build_confirmation(state.data)
        else:
            return "Would you like pickup or delivery?"

    elif step == "address":
        state = await update_conversation(supabase, state, "confirm", {"address": message})
        return _build_confirmation(state.data)

    elif step == "confirm":
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["yes", "confirm", "ok", "sure", "go ahead"]):
            # Place order via ihsanOS API
            result = await _place_order(client_bot, state.data, user)
            await clear_conversation(supabase, client_bot.client_id, chat_id)

            if result:
                return (
                    f"Order confirmed! #{result['order_number']}\n"
                    f"Total: ${state.data['total']:.2f}\n"
                    f"{'Delivery' if state.data.get('fulfillment') == 'delivery' else 'Pickup'}\n\n"
                    f"Track your order: {IHSANOS_API.replace('/api', '')}/shop/{client_bot.bot_username}/orders/{result['order_number']}"
                )
            else:
                return "Sorry, something went wrong placing your order. Please try again."
        elif any(w in msg_lower for w in ["no", "cancel", "change"]):
            await clear_conversation(supabase, client_bot.client_id, chat_id)
            return "Order cancelled. Send /order to start again."
        else:
            return "Please confirm your order (yes/no)."

    return "Something went wrong. Send /order to start over."


def _build_confirmation(data: dict) -> str:
    items = data.get("items", [])
    total = data.get("total", 0)
    fulfillment = data.get("fulfillment", "pickup")
    address = data.get("address", "")

    text = "Please confirm your order:\n\n"
    for i in items:
        text += f"  {i['quantity']}x {i['name']} — ${i['price'] * i['quantity']:.2f}\n"
    text += f"\nTotal: ${total:.2f}"
    text += f"\n{'Delivery to: ' + address if fulfillment == 'delivery' else 'Pickup at store'}"
    text += "\n\nConfirm? (yes/no)"
    return text


async def _fetch_products(org_id: str | None) -> list[dict]:
    if not org_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{IHSANOS_API}/products/{org_id}")
            if resp.status_code == 200:
                return resp.json().get("products", resp.json() if isinstance(resp.json(), list) else [])
    except Exception as e:
        logger.error(f"Failed to fetch products: {e}")
    return []


async def _place_order(client_bot, data: dict, user) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{IHSANOS_API}/place-order", json={
                "org_id": client_bot.ihsanos_org_id,
                "source": "telegram",
                "fulfillment_type": data.get("fulfillment", "pickup"),
                "customer_name": user.name,
                "customer_phone": "",
                "customer_email": "",
                "delivery_address": data.get("address", ""),
                "payment_method": "cash",
                "items": [{"product_id": i["product_id"], "quantity": i["quantity"]} for i in data.get("items", [])],
            })
            if resp.status_code == 200:
                return resp.json().get("data")
    except Exception as e:
        logger.error(f"Failed to place order: {e}")
    return None
