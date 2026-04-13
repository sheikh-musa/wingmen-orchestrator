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

from notification_router import get_chat_id

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


async def _simplify(role: str, message: str, tags: list[str]) -> str:
    """Distill a technical council message into plain English for Musa.

    Musa is a firefighter trainer, not a developer reading code diffs.
    He needs: what's being proposed, what the disagreement is, and what
    action he can take — in 3-5 sentences, no jargon.
    """
    try:
        from ai_provider import call_ai

        role_label = ROLE_LABELS.get(role, role)
        tags_str = ", ".join(tags) if tags else "none"

        summary = await call_ai(
            f"""Summarize this council message in plain English for a non-technical founder.
He needs to understand: what is being said, why it matters, and what (if anything) he should do.

Role: {role_label}
Tags: {tags_str}
Message:
{message[:4000]}

Rules:
- 3-5 sentences maximum
- No code, no SQL, no file paths, no technical jargon
- Use simple analogies if the concept is complex
- End with what Musa should do (if anything) — e.g., "You don't need to act yet" or "Reply /rule to override"
- Write like you're explaining to a smart person who doesn't code""",
            system="You are a translator from technical to plain English. Be concise and clear.",
            model="fast",
            max_tokens=200,
        )
        return (summary or "").strip()
    except Exception as e:
        logger.warning(f"simplify failed: {e}")
        # Fallback: first 300 chars of original
        return message[:300] + ("..." if len(message) > 300 else "")


async def relay_council_messages(sb) -> None:
    """Send unrelayed council messages to Musa's Telegram. Fail-soft."""
    musa_id = get_chat_id("cto")
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

            # Simplify for Musa — plain English, no jargon
            plain = await _simplify(role, message, tags)

            text = (
                f"<b>Session {session_id} · Round {round_num}</b> {tags_str}\n"
                f"{role_label}\n\n"
                f"{_html_escape(plain)}"
            )

            # Add action hints based on what happened
            if role == "claude_ai" and not session_info.get("ended_at"):
                # Detect if questions are directed at Musa
                has_questions = "question" in message.lower() and ("musa" in message.lower() or "?" in message)
                question_hint = ""
                if has_questions or "ESCALATE" in tags or "INSUFFICIENT_CONTEXT" in tags:
                    question_hint = (
                        f"\n<code>/reply {session_id} &lt;your answer&gt;</code> to respond"
                    )

                if "ESCALATE" in tags:
                    text += (
                        f"\n\n<i>Al-Mushtashir escalated — needs your input.</i>"
                        f"{question_hint}\n"
                        f"<code>/rule {session_id} &lt;your decision&gt;</code> to decide"
                    )
                elif "PUSHBACK" in tags:
                    text += (
                        f"\n\n<i>Al-Mushtashir pushed back. Claude Code will respond.</i>"
                        f"{question_hint}\n"
                        f"<code>/rule {session_id} &lt;your decision&gt;</code> to override"
                    )
                elif "CONCUR" in tags:
                    text += (
                        f"\n\n<i>Consensus reached.</i>\n"
                        f"<code>/concur {session_id}</code> to approve"
                    )
                elif "INSUFFICIENT_CONTEXT" in tags:
                    text += (
                        f"\n\n<i>More context needed. Questions for you above.</i>"
                        f"{question_hint}"
                    )
                elif "SPEC_APPROVED" in tags:
                    text += (
                        f"\n\n<i>Spec approved by Al-Mushtashir.</i>\n"
                        f"<code>/concur {session_id}</code> to trigger dry-run"
                    )
            elif role == "claude_code" and not session_info.get("ended_at"):
                has_questions = "question" in message.lower() and "musa" in message.lower()
                if has_questions:
                    text += (
                        f"\n\n<i>Claude Code has questions for you.</i>\n"
                        f"<code>/reply {session_id} &lt;your answer&gt;</code>"
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
