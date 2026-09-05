"""The /tools command — Musa-only Telegram summary of the Wingmen
feature inventory with health signals.

Registered in cto_bot.py alongside /concur /rule /halt. Reads
wingmen_features, groups by stage + health_signal, and posts a
one-message summary with health-green unknowns flagged as
candidates for Musa's promotion review. Links back to the
superadmin tools page for the full view and promote actions.

Origin: CTO Council session 2 option 4 decision (2026-04-12).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from supabase import acreate_client
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger("wingmen.tools_command")

TOOLS_URL = "https://bapa.ihsanos.com/super-admin/tools"
STALE_HEALTH_HOURS = 2


def _env(name: str) -> str:
    """Read env var at call time + strip inline comments (matches the
    pattern established in council_commands._env)."""
    raw = os.environ.get(name, "")
    return raw.split("#", 1)[0].strip()


def _is_musa(update: Update) -> bool:
    musa_id = _env("MUSA_TELEGRAM_ID")
    return bool(musa_id) and str(update.effective_user.id) == musa_id


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def cmd_tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tools — one-message summary of wingmen_features."""
    if not _is_musa(update):
        await update.message.reply_text(
            "Tools inventory is Musa-only."
        )
        return

    try:
        sb = await acreate_client(
            _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY")
        )
    except Exception as e:
        logger.error(f"/tools supabase client failed: {e}")
        await update.message.reply_text(
            f"❌ Failed to connect to Supabase: {e}"
        )
        return

    try:
        result = await (
            sb.table("wingmen_features")
            .select(
                "slug, display_name, category, stage, health_signal, "
                "health_signal_computed_at, needs_backfill"
            )
            .execute()
        )
    except Exception as e:
        logger.error(f"/tools query failed: {e}")
        await update.message.reply_text(
            f"❌ Failed to load features: {e}"
        )
        return

    features = result.data or []
    if not features:
        await update.message.reply_text(
            "No features registered in wingmen_features."
        )
        return

    # Count by stage
    stage_counts: dict[str, int] = {}
    for f in features:
        s = f.get("stage") or "unknown"
        stage_counts[s] = stage_counts.get(s, 0) + 1

    stage_emoji = {
        "ga": "🟢",
        "beta": "🟠",
        "dogfood": "🟡",
        "green_code": "🔴",
        "unknown": "◌",
        "deprecated": "⊘",
    }

    stage_line = " · ".join(
        f"{stage_emoji.get(s, '?')} {s}: {n}"
        for s, n in sorted(
            stage_counts.items(),
            key=lambda kv: ["ga", "beta", "dogfood", "green_code", "unknown", "deprecated"].index(kv[0])
            if kv[0]
            in ["ga", "beta", "dogfood", "green_code", "unknown", "deprecated"]
            else 99,
        )
    )

    # Find health-green unknowns (candidates for promotion review)
    # Rule: health_signal='ok', stage='unknown', not stale (computed_at
    # within STALE_HEALTH_HOURS), not in needs_backfill state.
    now = datetime.now(timezone.utc)
    candidates: list[dict] = []
    alerts: list[dict] = []
    for f in features:
        hs = f.get("health_signal")
        stage = f.get("stage")
        computed_at_str = f.get("health_signal_computed_at")
        if computed_at_str:
            try:
                computed_at = datetime.fromisoformat(
                    computed_at_str.replace("Z", "+00:00")
                )
                is_stale = (now - computed_at).total_seconds() > STALE_HEALTH_HOURS * 3600
            except ValueError:
                is_stale = True
        else:
            is_stale = True

        if hs == "ok" and stage == "unknown" and not is_stale:
            candidates.append(f)
        if hs == "alert":
            alerts.append(f)

    # Build the message
    lines: list[str] = []
    lines.append(f"🧰 <b>Wingmen Tools</b> ({len(features)} total)")
    lines.append(stage_line)
    lines.append("")

    if candidates:
        lines.append(
            f"<b>⚠️ Health-green unknowns ({len(candidates)}) — ready for review:</b>"
        )
        for f in candidates[:5]:
            slug = f.get("slug", "?")
            name = _html_escape(f.get("display_name", slug))
            lines.append(f"  • <code>{slug}</code> — {name}")
        if len(candidates) > 5:
            lines.append(f"  …and {len(candidates) - 5} more")
        lines.append("")
    else:
        lines.append("✅ No health-green unknowns — all unknowns lack signal")
        lines.append("")

    if alerts:
        lines.append(f"<b>🔴 Health-alert ({len(alerts)}):</b>")
        for f in alerts[:5]:
            slug = f.get("slug", "?")
            name = _html_escape(f.get("display_name", slug))
            lines.append(f"  • <code>{slug}</code> — {name}")
        if len(alerts) > 5:
            lines.append(f"  …and {len(alerts) - 5} more")
    else:
        lines.append("🟢 No health alerts")

    lines.append("")
    lines.append(f"→ <a href=\"{TOOLS_URL}\">Full view + promote</a>")

    msg = "\n".join(lines)

    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
