"""Council summary — sends plain-language Telegram summaries of ended CTO
Council sessions to Musa via the orchestrator bot.

Called from wingmen_orch.py main loop every poll. Queries for sessions
where ended_at IS NOT NULL and summary_sent_at IS NULL, generates a
plain-language summary via Anthropic (Haiku), and sends via Telegram.
Marks summary_sent_at on success so each session is only summarized once.

Origin: council session 1 (2026-04-11). Musa wanted decision visibility
in Telegram so he can react without reading council threads directly.
Reply commands /concur /rule /halt live in cto_bot.py.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode

from ai_provider import call_ai
from notification_router import get_chat_id

logger = logging.getLogger("wingmen.council_summary")

# Sessions that get summarized (decisions and escalations worth notifying)
# Skipped: circuit_breaker_tokens, circuit_breaker_usd, timeout — infrastructure
# alerts that belong in a different channel, not decision summaries.
SKIP_ENDED_REASONS = {"circuit_breaker_tokens", "circuit_breaker_usd", "timeout"}


def _config() -> tuple[str, str]:
    """Read MUSA_TELEGRAM_ID and TELEGRAM_BOT_TOKEN at CALL TIME, not
    at module load time, AND defensively strip any trailing inline
    "#" comment and whitespace in case python-dotenv leaves them in.

    Bug history (2026-04-12): two bugs, one fix.
    (1) Module-level constants were captured before load_dotenv() had
    run in wingmen_orch.py, so both values were empty strings and the
    summary task returned silently on every poll.
    (2) The .env file had inline "# comment" on these lines; the
    council_commands._is_musa path tripped on this separately because
    its permission check compared the user id to the raw value.
    Fix both: read at call time, strip inline comments defensively.
    """
    def clean(name: str) -> str:
        raw = os.environ.get(name, "")
        return raw.split("#", 1)[0].strip()
    return (get_chat_id("cto") or "", clean("TELEGRAM_BOT_TOKEN"))


async def summarize_pending_sessions(sb) -> None:
    """Find newly-ended council sessions and send summaries to Musa.

    Fail-soft: logs errors but never raises. The main loop calls this
    every poll and must not be blocked by a telegram or anthropic hiccup.
    """
    musa_id, bot_token = _config()
    if not musa_id or not bot_token:
        return

    try:
        result = await (
            sb.table("cto_council_sessions")
            .select(
                "id, public_id, opening_prompt, ended_at, ended_reason, "
                "current_round, max_rounds, token_count, usd_estimated, "
                "had_pushback, repo, execution_status, spec_approved_at"
            )
            .not_.is_("ended_at", "null")
            .is_("summary_sent_at", "null")
            .execute()
        )
    except Exception as e:
        logger.error(f"Council summary query failed: {e}")
        return

    sessions = result.data or []
    if not sessions:
        return

    for session in sessions:
        session_id = session["id"]
        ended_reason = session.get("ended_reason") or "unknown"

        # Skip infrastructure-alert sessions — still mark them summarized
        # so we don't re-query every poll.
        if ended_reason in SKIP_ENDED_REASONS:
            try:
                await (
                    sb.table("cto_council_sessions")
                    .update({"summary_sent_at": datetime.now(timezone.utc).isoformat()})
                    .eq("id", session_id)
                    .execute()
                )
                logger.info(
                    f"Council session {session_id} skipped ({ended_reason}) — no summary"
                )
            except Exception as e:
                logger.error(f"Failed to mark skipped session {session_id}: {e}")
            continue

        try:
            summary_text = await _generate_summary(sb, session)
            await _send_to_musa(session, summary_text, musa_id, bot_token)
            await (
                sb.table("cto_council_sessions")
                .update({"summary_sent_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", session_id)
                .execute()
            )
            logger.info(
                f"Council session {session_id} summary sent to Musa ({ended_reason})"
            )
        except Exception as e:
            logger.exception(
                f"Failed to summarize council session {session_id}: {e}"
            )
            # Leave summary_sent_at NULL — retry next poll.


async def _generate_summary(sb, session: dict) -> str:
    """Call Anthropic to produce a plain-language summary of the session."""
    session_id = session["id"]

    thread_result = await (
        sb.table("cto_council")
        .select("round, role, message, tags")
        .eq("session_id", session_id)
        .order("created_at", desc=False)
        .execute()
    )
    rows = thread_result.data or []

    role_labels = {
        "musa": "Musa",
        "claude_code": "Claude Code",
        "claude_ai": "Al-Mushtashir",
        "system": "System",
    }
    thread_lines = []
    for row in rows:
        label = role_labels.get(row["role"], row["role"])
        tags = " ".join(f"[{t}]" for t in (row.get("tags") or []))
        thread_lines.append(
            f"=== Round {row['round']} — {label} {tags} ===\n{row['message']}"
        )
    thread_text = "\n\n".join(thread_lines)
    if len(thread_text) > 30000:
        thread_text = thread_text[:30000] + "\n\n[... thread truncated]"

    system_prompt = (
        "You are the neutral clerk of the Wingmen CTO Council. You summarize "
        "council session threads in plain language for Musa, the human founder. "
        "Musa reads these in Telegram so he can decide quickly without opening "
        "the full thread.\n\n"
        "Your summary must:\n"
        "1. State the question in one sentence.\n"
        "2. Summarize what Claude Code proposed and what Al-Mushtashir pushed back on.\n"
        "3. State what was decided if consensus was reached, or explain the open "
        "question if it was escalated or stalled.\n"
        "4. Be specific about the disagreement and its resolution. No fluff.\n"
        "5. Be 4-8 sentences total. Plain English.\n"
        "6. Do NOT use markdown formatting — no **, no #, no backticks. "
        "Plain text only (the Telegram client uses HTML mode and markdown will "
        "render broken)."
    )

    user_prompt = (
        f"Session {session_id} ended with reason: {session.get('ended_reason')}.\n"
        f"Rounds used: {session.get('current_round')}/{session.get('max_rounds')}.\n"
        f"Had pushback: {session.get('had_pushback')}.\n\n"
        f"Thread:\n\n{thread_text}\n\n"
        f"Write the summary now. Plain English, 4-8 sentences."
    )

    summary = await call_ai(
        user_prompt,
        system=system_prompt,
        model="fast",
        max_tokens=700,
    )
    return (summary or "").strip()


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _send_to_musa(
    session: dict, summary_text: str, musa_id: str, bot_token: str
) -> None:
    bot = Bot(token=bot_token)
    session_id = session["id"]
    ended_reason = session.get("ended_reason") or "unknown"
    rounds_str = f"{session.get('current_round', 0)}/{session.get('max_rounds', 0)}"
    tokens = session.get("token_count") or 0
    usd = float(session.get("usd_estimated") or 0)
    repo = session.get("repo") or "cross-repo"
    opening = (session.get("opening_prompt") or "")[:300]

    emoji = {
        "consensus": "✅",
        "musa_ruling": "🧑‍⚖️",
        "escalated": "⚠️",
        "max_rounds": "⏱",
        "musa_halt": "🛑",
    }.get(ended_reason, "🧠")

    # For decisions that still need Musa's input, append command hints.
    # For sessions Musa already ruled on, no hints — just the record.
    exec_status = session.get("execution_status")
    spec_approved = session.get("spec_approved_at")

    if exec_status:
        exec_emoji = {
            "dry_run_complete": "🟡",
            "dry_run_rejected": "🔴",
            "complete": "✅",
            "failed": "❌",
            "rolled_back": "⏪",
        }.get(exec_status, "⚙️")
        exec_line = f"\n\n{exec_emoji} <b>Execution: {_html_escape(exec_status)}</b>"
        if exec_status == "dry_run_complete":
            exec_line += f"\n<code>/execute {session_id} &lt;reason&gt;</code> — approve execution"
    else:
        exec_line = ""

    if ended_reason in ("consensus", "escalated", "max_rounds"):
        hints = (
            f"\n\n<b>Your move</b>\n"
            f"<code>/concur {session_id}</code> — approve &amp; ship\n"
            f"<code>/rule {session_id} &lt;text&gt;</code> — override\n"
            f"<code>/halt {session_id}</code> — stop"
        )
    else:
        hints = ""

    msg = (
        f"{emoji} <b>Council Session {session_id} — {_html_escape(ended_reason)}</b>\n"
        f"<i>{_html_escape(repo)}</i> · {rounds_str} rounds · "
        f"{tokens} tokens · ${usd:.4f}\n\n"
        f"<b>Question</b>\n{_html_escape(opening)}\n\n"
        f"<b>Summary</b>\n{_html_escape(summary_text)}"
        f"{exec_line}"
        f"{hints}"
    )

    await bot.send_message(
        chat_id=int(musa_id),
        text=msg,
        parse_mode=ParseMode.HTML,
    )
