"""Council reply commands — /concur /rule /halt for Musa's Telegram bot.

These are python-telegram-bot CommandHandler functions that let Musa
respond to council summaries from inside Telegram. Registered in
cto_bot.py alongside the other command handlers.

Permission model: all three commands are restricted to MUSA_TELEGRAM_ID.
Anyone else gets a polite rejection.

Flow:
  /concur <id>      -> posts role='musa' [CONCUR], ends session (if open)
  /rule <id> <text> -> posts role='musa' [RULING] with text, ends session
  /halt <id>        -> posts role='musa' [HALT], ends session

v1 known limitation: the system prompt's "When Musa Rules" section
asks Al-Mushtashir to emit a [SYNTHESIS] row after a ruling. v1 does
not auto-kick the strategist for the synthesis, because the common
case (consensus already ended the session) makes the pg trigger refuse
to fire. The decision is adequately captured by Musa's ruling row +
the Telegram summary + the full thread. If the structured [SYNTHESIS]
record becomes missed operationally, add a Python-side synthesis
generator in v2 that bypasses the edge function entirely.

Origin: council session 1 (2026-04-11). Paired with
nervous_system/council_summary.py.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from supabase import acreate_client
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("wingmen.council_commands")

MUSA_TELEGRAM_ID = os.environ.get("MUSA_TELEGRAM_ID", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


async def _get_sb():
    return await acreate_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _is_musa(update: Update) -> bool:
    if not MUSA_TELEGRAM_ID:
        return False
    return str(update.effective_user.id) == str(MUSA_TELEGRAM_ID)


async def _reject(update: Update, reason: str) -> None:
    await update.message.reply_text(
        f"Council commands are restricted to Musa. ({reason})"
    )


async def _load_session(sb, session_id: int) -> dict | None:
    result = await (
        sb.table("cto_council_sessions")
        .select("id, current_round, max_rounds, ended_at, ended_reason, opening_prompt")
        .eq("id", session_id)
        .maybe_single()
        .execute()
    )
    return result.data if result else None


async def _post_musa_row(
    sb, session_id: int, message: str, tag: str, round_num: int
) -> None:
    await (
        sb.table("cto_council")
        .insert(
            {
                "session_id": session_id,
                "round": round_num,
                "role": "musa",
                "message": message,
                "tags": [tag],
                "context": {"source": "telegram_command"},
            }
        )
        .execute()
    )


async def _end_session(sb, session_id: int, reason: str) -> None:
    await (
        sb.table("cto_council_sessions")
        .update(
            {
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "ended_reason": reason,
            }
        )
        .eq("id", session_id)
        .execute()
    )


async def _resolve_session_id_arg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if not context.args:
        await update.message.reply_text(
            "Usage: /concur <session_id>  (or /rule <session_id> <text>, or /halt <session_id>)"
        )
        return None
    try:
        return int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            f"First arg must be a session id (integer). Got: {context.args[0]!r}"
        )
        return None


# ── Public command handlers ─────────────────────────────────────────


async def cmd_concur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/concur <session_id> — Musa approves the consensus."""
    if not _is_musa(update):
        await _reject(update, "not-musa")
        return

    session_id = await _resolve_session_id_arg(update, context)
    if session_id is None:
        return

    sb = await _get_sb()
    session = await _load_session(sb, session_id)
    if session is None:
        await update.message.reply_text(f"Session {session_id} not found.")
        return

    current_round = session.get("current_round") or 0
    next_round = current_round + 1

    await _post_musa_row(
        sb, session_id,
        "Approved. Ship it.",
        "CONCUR",
        next_round,
    )

    if not session.get("ended_at"):
        await _end_session(sb, session_id, "musa_ruling")

    await update.message.reply_text(
        f"✅ Concurred on session {session_id}. Decision recorded."
    )


async def cmd_rule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/rule <session_id> <text> — Musa rules with an explicit decision."""
    if not _is_musa(update):
        await _reject(update, "not-musa")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /rule <session_id> <your decision text>"
        )
        return

    try:
        session_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            f"First arg must be a session id (integer). Got: {context.args[0]!r}"
        )
        return

    ruling_text = " ".join(context.args[1:]).strip()
    if not ruling_text:
        await update.message.reply_text("Ruling text cannot be empty.")
        return

    sb = await _get_sb()
    session = await _load_session(sb, session_id)
    if session is None:
        await update.message.reply_text(f"Session {session_id} not found.")
        return

    current_round = session.get("current_round") or 0
    next_round = current_round + 1

    await _post_musa_row(
        sb, session_id,
        f"[RULING] {ruling_text}",
        "RULING",
        next_round,
    )

    if not session.get("ended_at"):
        await _end_session(sb, session_id, "musa_ruling")

    await update.message.reply_text(
        f"🧑‍⚖️ Ruled on session {session_id}. Decision recorded."
    )


async def cmd_halt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/halt <session_id> — Musa stops the session without a decision."""
    if not _is_musa(update):
        await _reject(update, "not-musa")
        return

    session_id = await _resolve_session_id_arg(update, context)
    if session_id is None:
        return

    sb = await _get_sb()
    session = await _load_session(sb, session_id)
    if session is None:
        await update.message.reply_text(f"Session {session_id} not found.")
        return

    current_round = session.get("current_round") or 0
    next_round = current_round + 1

    await _post_musa_row(
        sb, session_id,
        "Halted by Musa. No decision recorded.",
        "HALT",
        next_round,
    )

    if not session.get("ended_at"):
        await _end_session(sb, session_id, "musa_halt")

    await update.message.reply_text(
        f"🛑 Halted session {session_id}. No synthesis."
    )
