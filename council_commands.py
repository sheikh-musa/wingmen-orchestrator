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


def _env(name: str) -> str:
    """Read env var at CALL TIME, not module load time. Strips whitespace
    and any trailing inline "#" comment for belt-and-suspenders parity
    with python-dotenv's inline-comment handling. Fixes the same
    import-order bug caught in council_summary.py (2026-04-12)."""
    raw = os.environ.get(name, "")
    return raw.split("#", 1)[0].strip()


async def _get_sb():
    return await acreate_client(_env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY"))


def _is_musa(update: Update) -> bool:
    musa_id = _env("MUSA_TELEGRAM_ID")
    if not musa_id:
        return False
    return str(update.effective_user.id) == musa_id


async def cmd_council(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/council <question> — start a new council session from Telegram.

    The autonomous agent + Al-Mushtashir handle the discussion. Musa
    watches rounds in plain English via the relay. /rule to override,
    /concur to approve.
    """
    if not _is_musa(update):
        await _reject(update, "not-musa")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /council <question>\n"
            "       /council repo:<name> <question>\n\n"
            "Examples:\n"
            "  /council Should we prioritize TDU or BAPA this week?\n"
            "  /council repo:ihsanos How should the qurban booking flow handle partial payments?\n"
            "  /council repo:dookana What's the MVP onboarding for micro-merchants?\n\n"
            "Best for decisions with tradeoffs, not bug fixes or status checks.\n"
            "Available repos: ihsanos, dookana, orchestrator, cosem-tdu"
        )
        return

    # Check for repo: prefix
    args_text = " ".join(context.args).strip()
    repo_name = "orchestrator"  # default
    if args_text.startswith("repo:"):
        parts = args_text.split(" ", 1)
        repo_name = parts[0].replace("repo:", "").strip()
        question = parts[1].strip() if len(parts) > 1 else ""
    else:
        question = args_text
    if len(question) < 10:
        await update.message.reply_text("Question is too short. Be specific.")
        return

    sb = await _get_sb()

    # Create session
    session_result = await (
        sb.table("cto_council_sessions")
        .insert({
            "opening_prompt": question,
            "max_rounds": 6,
            "repo": repo_name,
            "tags": ["telegram-initiated"],
        })
        .select("id, public_id")
        .single()
        .execute()
    )
    session_id = session_result.data["id"]

    # Post the opening as a musa row
    await sb.table("cto_council").insert({
        "session_id": session_id,
        "round": 0,
        "role": "musa",
        "message": question,
        "tags": ["OPENING"],
        "context": {"source": "telegram_council_command"},
    }).execute()

    # Post an initial claude_code row to kick off the discussion.
    # The autonomous agent will see this is a fresh session and the
    # trigger will fire Al-Mushtashir on this row.
    await sb.table("cto_council").insert({
        "session_id": session_id,
        "round": 1,
        "role": "claude_code",
        "message": (
            f"Musa opened this session from Telegram: {question}\n\n"
            "I don't have a strong opening position yet. Let me hear "
            "Al-Mushtashir's framing before I commit. Evaluate this "
            "against CTO principles, the brain snapshot, and current "
            "repo state."
        ),
        "tags": [],
        "context": {"source": "telegram_council_command"},
    }).execute()

    await update.message.reply_text(
        f"🧠 Council session {session_id} started.\n\n"
        f"❓ {question}\n\n"
        f"Al-Mushtashir will respond in ~10s. The autonomous agent "
        f"drives the discussion. You'll see every round in plain English.\n\n"
        f"/rule {session_id} <text> — override at any point\n"
        f"/concur {session_id} — approve when ready\n"
        f"/halt {session_id} — stop"
    )


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


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reply <session_id> <text> — answer a question or add context without ending the session.

    Unlike /rule (which overrides) or /concur (which approves), /reply
    just adds Musa's input to the thread. The discussion continues.
    The autonomous agent sees Musa's reply and factors it into the
    next round. Al-Mushtashir sees it as a musa row in the thread.
    """
    if not _is_musa(update):
        await _reject(update, "not-musa")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /reply <session_id> <your answer or context>\n\n"
            "This adds your input to the discussion without ending it."
        )
        return

    try:
        session_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(f"First arg must be a session id. Got: {context.args[0]!r}")
        return

    reply_text = " ".join(context.args[1:]).strip()
    if not reply_text:
        await update.message.reply_text("Reply text cannot be empty.")
        return

    sb = await _get_sb()
    session = await _load_session(sb, session_id)
    if session is None:
        await update.message.reply_text(f"Session {session_id} not found.")
        return

    if session.get("ended_at"):
        await update.message.reply_text(f"Session {session_id} has already ended. Start a new one.")
        return

    current_round = session.get("current_round") or 0
    await _post_musa_row(sb, session_id, reply_text, "REPLY", current_round + 1)

    await update.message.reply_text(
        f"💬 Reply posted to session {session_id}. Discussion continues."
    )


async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/execute <session_id> <comment> — trigger real execution after dry-run approval."""
    if not _is_musa(update):
        await _reject(update, "not-musa")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /execute <session_id> <reason why you approve execution>"
        )
        return

    try:
        session_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            f"First arg must be a session id. Got: {context.args[0]!r}"
        )
        return

    comment = " ".join(context.args[1:]).strip()
    if not comment:
        await update.message.reply_text(
            "Comment is required — explain why you approve this execution."
        )
        return

    sb = await _get_sb()
    session = await _load_session(sb, session_id)
    if session is None:
        await update.message.reply_text(f"Session {session_id} not found.")
        return

    # Verify the session is in dry_run_complete state
    result = await (
        sb.table("cto_council_sessions")
        .select("execution_status, spec_json, spec_approved_at")
        .eq("id", session_id)
        .single()
        .execute()
    )
    session_data = result.data if result else None

    if not session_data or not session_data.get("spec_json"):
        await update.message.reply_text(
            f"Session {session_id} has no spec. Cannot execute."
        )
        return

    if not session_data.get("spec_approved_at"):
        await update.message.reply_text(
            f"Session {session_id} spec not yet approved by Al-Mushtashir."
        )
        return

    exec_status = session_data.get("execution_status")
    if exec_status != "dry_run_complete":
        await update.message.reply_text(
            f"Session {session_id} is in status '{exec_status}', not 'dry_run_complete'. "
            f"Cannot execute until dry-run is approved."
        )
        return

    # Set status to executing — the executor picks it up on next poll
    await (
        sb.table("cto_council_sessions")
        .update({"execution_status": "executing"})
        .eq("id", session_id)
        .execute()
    )

    # Post Musa's approval as an audit row
    current_round = session.get("current_round") or 0
    await _post_musa_row(
        sb, session_id,
        f"[EXECUTE] Approved execution. Reason: {comment}",
        "EXECUTE",
        current_round + 1,
    )

    await update.message.reply_text(
        f"⚡ Execution approved for session {session_id}. "
        f"The executor will pick it up within ~60s.\n"
        f"Reason: {comment}"
    )
