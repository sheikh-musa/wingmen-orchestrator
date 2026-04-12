"""Council Relay — sends every council message to Musa's Telegram in real-time.

Unlike council_summary (which fires AFTER a session ends), the relay
sends each individual round as it happens: Claude Code proposes →
Musa sees it in Telegram → Al-Mushtashir pushes back → Musa sees
that too → Musa can /rule or /concur at any point mid-discussion.

Polls cto_council for rows where telegram_message_id IS NULL, sends
each one, and marks it with the Telegram message ID on success.

Origin: Musa's feedback (2026-04-13): "how can i concur if i never
receive the messages? maybe i should be looking at the messages
during the session so i can have realtime input"
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode

logger = logging.getLogger("wingmen.council_relay")


def _env(name: str) -> str:
    raw = os.environ.get(name, "")
    return raw.split("#", 1)[0].strip()


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


ROLE_LABELS = {
    "musa": "👤 Musa",
    "claude_code": "🛠️ Claude Code",
    "claude_ai": "🧠 Al-Mushtashir",
    "system": "⚙️ System",
}


async def relay_council_messages(sb) -> None:
    """Send unrelayed council messages to Musa's Telegram. Fail-soft."""
    musa_id = _env("MUSA_TELEGRAM_ID")
    bot_token = _env("TELEGRAM_BOT_TOKEN")
    if not musa_id or not bot_token:
        return

    try:
        # Find unrelayed messages from OPEN sessions (don't relay old closed sessions)
        result = await (
            sb.table("cto_council")
            .select(
                "id, session_id, round, role, message, tags, created_at, "
                "cto_council_sessions!inner(ended_at, opening_prompt)"
            )
            .is_("telegram_message_id", "null")
            .order("created_at", desc=False)
            .limit(10)  # batch size per poll
            .execute()
        )
    except Exception as e:
        logger.error(f"council_relay query failed: {e}")
        return

    rows = result.data or []
    if not rows:
        return

    bot = Bot(token=bot_token)

    for row in rows:
        try:
            session_id = row["session_id"]
            role = row.get("role", "?")
            round_num = row.get("round", 0)
            tags = row.get("tags") or []
            message = row.get("message", "")
            session_info = row.get("cto_council_sessions") or {}

            role_label = ROLE_LABELS.get(role, role)
            tags_str = " ".join(f"[{t}]" for t in tags) if tags else ""

            # Truncate long messages for Telegram (4096 char limit)
            preview = message
            if len(preview) > 3000:
                preview = preview[:3000] + "\n\n... (truncated — full message in council thread)"

            text = (
                f"<b>Session {session_id} · Round {round_num}</b> {tags_str}\n"
                f"{role_label}\n\n"
                f"{_html_escape(preview)}"
            )

            # Add action hints for Al-Mushtashir responses
            if role == "claude_ai" and not session_info.get("ended_at"):
                if "PUSHBACK" in tags:
                    text += (
                        f"\n\n<i>Al-Mushtashir pushed back. Claude Code will respond.</i>\n"
                        f"<code>/rule {session_id} &lt;your decision&gt;</code> to override"
                    )
                elif "CONCUR" in tags:
                    text += (
                        f"\n\n<i>Consensus reached.</i>\n"
                        f"<code>/concur {session_id}</code> to approve"
                    )
                elif "INSUFFICIENT_CONTEXT" in tags:
                    text += f"\n\n<i>More context requested. Discussion continues.</i>"
                elif "SPEC_APPROVED" in tags:
                    text += (
                        f"\n\n<i>Spec approved by Al-Mushtashir.</i>\n"
                        f"<code>/concur {session_id}</code> to trigger dry-run"
                    )

            sent = await bot.send_message(
                chat_id=int(musa_id),
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            # Mark as relayed
            await (
                sb.table("cto_council")
                .update({"telegram_message_id": sent.message_id})
                .eq("id", row["id"])
                .execute()
            )

        except Exception as e:
            logger.error(f"council_relay failed for row {row.get('id')}: {e}")
            # Continue with next row — don't block on one failure
