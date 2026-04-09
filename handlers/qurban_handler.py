"""Qurban Handler — conversational qurban booking flow.

Steps: show_animals -> select -> collect_niyyah -> distribution -> confirm -> booked
"""

from __future__ import annotations

import logging
import httpx
from ai_provider import call_ai, extract_json
from conversation import ConversationState, start_conversation, update_conversation, clear_conversation

logger = logging.getLogger("wingmen.handlers.qurban")

IHSANOS_API = "https://ihsanos.com/api"


async def handle(supabase, bot, chat_id: str, message: str, state: ConversationState | None, user, client_bot) -> str:
    """Process a qurban booking message."""

    if state is None or state.flow != "qurban_booking":
        state = await start_conversation(supabase, client_bot.client_id, chat_id, "qurban_booking", "show_animals")

    step = state.step

    if step == "show_animals":
        animals = await _fetch_animals(client_bot.ihsanos_org_id)
        if not animals:
            await clear_conversation(supabase, client_bot.client_id, chat_id)
            return "Qurban is not currently available. Please check back later."

        state = await update_conversation(supabase, state, "select", {"animals": animals})

        flags = {"ID": "ID", "PK": "PK", "AU": "AU", "NZ": "NZ", "SG": "SG"}
        text = "Qurban bookings available:\n\n"
        for i, a in enumerate(animals, 1):
            flag = flags.get(a.get("country", ""), a.get("country", "?"))
            shares_left = a.get("total_available", 0) - a.get("total_booked", 0)
            text += f"{i}. [{flag}] {a['animal_type'].title()} ({a.get('country', '?')}) — ${a['org_price']:.0f}/share ({shares_left} left)\n"
        text += "\nWhich would you like? (number or name)"
        return text

    elif step == "select":
        animals = state.data.get("animals", [])
        # Try to match by number or name
        selected = None
        try:
            idx = int(message.strip()) - 1
            if 0 <= idx < len(animals):
                selected = animals[idx]
        except ValueError:
            msg_lower = message.lower()
            for a in animals:
                if a["animal_type"] in msg_lower or a.get("country", "").lower() in msg_lower:
                    selected = a
                    break

        if not selected:
            return "I didn't catch that. Please choose by number or animal type."

        state = await update_conversation(supabase, state, "collect_niyyah", {"selected_animal": selected, "niyyah": []})
        return f"You selected: {selected['animal_type'].title()} ({selected.get('country', '?')}) — ${selected['org_price']:.0f}\n\nWho is this qurban for? (e.g., 'my late father Ahmad bin Ismail')"

    elif step == "collect_niyyah":
        # Check if user is done adding niyyah
        if message.lower().strip() in ("done", "confirm", "that's all", "no more"):
            animal = state.data.get("selected_animal", {})
            dist_options = animal.get("distribution_options") or ["donate_locally"]

            state = await update_conversation(supabase, state, "distribution")

            text = "How would you like the meat distributed?\n"
            for i, opt in enumerate(dist_options, 1):
                label = opt.replace("_", " ").title()
                text += f"  {i}. {label}\n"
            return text

        # Use AI to parse niyyah from natural language
        parsed = await call_ai(
            f'Parse this qurban niyyah intention into JSON.\n\nUser said: "{message}"\n\nReturn: {{"recipient_name": "...", "relationship": "self|parent|spouse|child|sibling|relative|other", "is_deceased": true|false}}',
            model="fast",
            json_mode=True,
        )
        niyyah_entry = extract_json(parsed)

        if not niyyah_entry or not isinstance(niyyah_entry, dict):
            return "I didn't understand. Please tell me who this qurban is for, e.g., 'my late father Ahmad bin Ismail' or 'myself'"

        current_niyyah = state.data.get("niyyah", [])
        current_niyyah.append(niyyah_entry)

        deceased_label = " (Marhum)" if niyyah_entry.get("is_deceased") else ""
        state = await update_conversation(supabase, state, "collect_niyyah", {"niyyah": current_niyyah})

        return (
            f"Niyyah recorded: {niyyah_entry['recipient_name']} ({niyyah_entry['relationship']}){deceased_label}\n\n"
            f"Add another niyyah, or say 'done' to continue."
        )

    elif step == "distribution":
        animal = state.data.get("selected_animal", {})
        dist_options = animal.get("distribution_options") or ["donate_locally"]

        # Match selection
        selected_dist = dist_options[0]  # default
        try:
            idx = int(message.strip()) - 1
            if 0 <= idx < len(dist_options):
                selected_dist = dist_options[idx]
        except ValueError:
            msg_lower = message.lower()
            for opt in dist_options:
                if opt.replace("_", " ") in msg_lower or msg_lower in opt:
                    selected_dist = opt
                    break

        if selected_dist == "ship_to_donor":
            state = await update_conversation(supabase, state, "shipping_address", {"distribution": selected_dist})
            return "What's your shipping address?"

        state = await update_conversation(supabase, state, "confirm", {"distribution": selected_dist})
        return _build_qurban_confirmation(state.data)

    elif step == "shipping_address":
        state = await update_conversation(supabase, state, "confirm", {"shipping_address": message})
        return _build_qurban_confirmation(state.data)

    elif step == "confirm":
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["yes", "confirm", "book", "go"]):
            result = await _book_qurban(client_bot, state.data, user)
            await clear_conversation(supabase, client_bot.client_id, chat_id)

            if result:
                return (
                    f"Qurban booked! #{result['booking_ref']}\n"
                    f"Amount: ${result['amount']:.0f}\n\n"
                    f"Track: {IHSANOS_API.replace('/api', '')}/shop/{client_bot.bot_username}/qurban/track/{result['booking_ref']}"
                )
            return "Sorry, something went wrong. Please try again."
        elif any(w in msg_lower for w in ["no", "cancel"]):
            await clear_conversation(supabase, client_bot.client_id, chat_id)
            return "Booking cancelled."
        return "Please confirm (yes/no)."

    return "Something went wrong. Send /qurban to start over."


def _build_qurban_confirmation(data: dict) -> str:
    animal = data.get("selected_animal", {})
    niyyah_list = data.get("niyyah", [])
    dist = data.get("distribution", "donate_locally").replace("_", " ").title()

    text = f"Please confirm your qurban booking:\n\n"
    text += f"Animal: {animal.get('animal_type', '?').title()} ({animal.get('country', '?')})\n"
    text += f"Price: ${animal.get('org_price', 0):.0f}\n"
    text += f"Distribution: {dist}\n"
    text += f"\nNiyyah:\n"
    for n in niyyah_list:
        deceased = " (Marhum)" if n.get("is_deceased") else ""
        text += f"  - {n['recipient_name']} ({n['relationship']}){deceased}\n"
    text += "\nConfirm booking? (yes/no)"
    return text


async def _fetch_animals(org_id: str | None) -> list[dict]:
    if not org_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{IHSANOS_API}/qurban-animals/{org_id}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("animals", data if isinstance(data, list) else [])
    except Exception as e:
        logger.error(f"Failed to fetch animals: {e}")
    return []


async def _book_qurban(client_bot, data: dict, user) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{IHSANOS_API}/qurban-book", json={
                "org_id": client_bot.ihsanos_org_id,
                "animal_id": data.get("selected_animal", {}).get("id"),
                "shares": 1,
                "distribution_mode": data.get("distribution", "donate_locally"),
                "shipping_address": data.get("shipping_address"),
                "customer_name": user.name,
                "customer_email": "",
                "customer_phone": "",
                "niyyah": data.get("niyyah", []),
            })
            if resp.status_code == 200:
                return resp.json().get("data")
    except Exception as e:
        logger.error(f"Failed to book qurban: {e}")
    return None
