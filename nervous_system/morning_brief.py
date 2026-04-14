"""morning_brief — 6AM strategic Telegram brief with CTO recommendations."""

from __future__ import annotations

import logging
import os
import sys
import time

# Ensure parent dir is on path for ai_provider import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_provider import call_ai
from nervous_system import error_tracker

logger = logging.getLogger("wingmen.nervous_system.morning_brief")

CTO_PRINCIPLES_PATH = os.path.join(os.path.dirname(__file__), "..", "CTO_PRINCIPLES.md")


def _build_status_summary(snapshot: dict) -> str:
    """Build the status overview section from a snapshot."""
    health_icon = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534"}
    lines = []
    for r in snapshot.get("repos", []):
        icon = health_icon.get(r.get("health", ""), "\u26aa")
        commit_info = f" — {r['commits_24h']} commits yesterday" if r.get("commits_24h", 0) > 0 else " — no activity"
        deploy = f", deployed" if r.get("deploy_url") else ""
        lines.append(f"{icon} {r['name']}{commit_info}{deploy}")
        for b in r.get("blockers", []):
            lines.append(f"   Blocked: {b}")

    jobs = snapshot.get("job_queue", [])
    if jobs:
        lines.append(f"\nJobs: {len(jobs)} active")
        for j in jobs[:3]:
            lines.append(f"  #{j.get('id', '?')} {j.get('repo_name', '')} — {j.get('status', '')}")

    clients = snapshot.get("clients", [])
    if clients:
        names = ", ".join(c.get("name", "?") for c in clients)
        lines.append(f"\nClients: {names}")

    return "\n".join(lines)


async def _get_strategic_analysis(status_summary: str) -> str:
    """Ask AI for strategic CTO recommendations."""
    principles = ""
    if os.path.exists(CTO_PRINCIPLES_PATH):
        with open(CTO_PRINCIPLES_PATH) as f:
            principles = f.read()

    system = "You are Musa's CTO — a Muslim technologist who thinks strategically about both business growth and community impact."
    if principles:
        system += f"\n\n{principles}"

    prompt = f"""Current state of all projects:
{status_summary}

Based on this:
1. What should Musa focus on today? (highest impact action)
2. What's at risk if nothing changes? (blockers aging, clients quiet)
3. What opportunity is being missed?

Keep it to 3-5 bullet points. Be decisive, not diplomatic. Start each with an action verb."""

    try:
        return await call_ai(prompt, system=system, model="fast", max_tokens=1024)
    except Exception as e:
        logger.error(f"Strategic analysis failed: {e}")
        error_tracker.track_exception("morning_brief.strategic_analysis", e)
        return "(Strategic analysis unavailable)"


async def morning_brief(supabase, bot, admin_chat_id: str):
    """Send 6AM morning brief to Musa via Telegram."""
    start = time.monotonic()
    logger.info("morning_brief starting...")

    try:
        # Get latest snapshot
        result = await supabase.table("wingmen_brain").select("*").order("snapshot_at", desc=True).limit(1).execute()
        if not result.data:
            logger.warning("No brain snapshot found — skipping morning brief")
            return

        snapshot = result.data[0]
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        # Freshness check
        freshness_warning = ""
        sync_time_str = snapshot.get("created_at") or snapshot.get("snapshot_at")
        if sync_time_str:
            try:
                sync_time = datetime.fromisoformat(sync_time_str.replace("Z", "+00:00"))
                age_minutes = (now - sync_time).total_seconds() / 60
                if age_minutes > 300:
                    freshness_warning = f"\n⚠️ STALE DATA: Snapshot is {age_minutes / 60:.0f}h old — treat as hints only\n"
                elif age_minutes > 60:
                    freshness_warning = f"\n⏳ Data as of {int(age_minutes)}min ago\n"
            except (ValueError, TypeError):
                freshness_warning = "\n⚠️ Cannot determine snapshot freshness\n"

        # Low confidence repos warning
        low_conf_repos = [r for r in snapshot.get("repos", []) if r.get("confidence") == "low"]
        conf_warning = ""
        if low_conf_repos:
            names = ", ".join(r.get("name", "?") for r in low_conf_repos)
            conf_warning = f"\n📉 Low confidence repos: {names} — check contradictions\n"

        # Context notes from last sync
        context_notes = snapshot.get("context_notes", "")
        notes_section = ""
        if context_notes:
            notes_section = f"\n📋 Changes since last sync:\n{context_notes}\n"

        # CTO questions from repos (surfaced for morning decision-making)
        cto_questions = []
        for r in snapshot.get("repos", []):
            for q in r.get("cto_questions", []):
                cto_questions.append(f"[{r.get('name', '?')}] {q}")
        questions_section = ""
        if cto_questions:
            questions_section = "\n❓ Questions for CTO:\n" + "\n".join(f"- {q}" for q in cto_questions) + "\n"

        # Part 1: Status overview
        status = _build_status_summary(snapshot)

        # Part 2: Strategic analysis (include CTO questions for context)
        analysis_input = status
        if cto_questions:
            analysis_input += "\n\nOpen questions from repos:\n" + "\n".join(f"- {q}" for q in cto_questions)
        analysis = await _get_strategic_analysis(analysis_input)

        # Compose message
        date_str = now.strftime("%d %b %Y")
        message = f"\u2600\ufe0f Wingmen Morning Brief — {date_str}\n{freshness_warning}{conf_warning}{notes_section}{questions_section}\n{status}\n\n\U0001f4a1 CTO Recommendations:\n{analysis}"

        # Send via Telegram — split long messages
        if len(message) <= 4000:
            await bot.send_message(chat_id=admin_chat_id, text=message)
        else:
            chunks = []
            current = ""
            for line in message.split("\n"):
                if len(current) + len(line) + 1 > 4000:
                    if current:
                        chunks.append(current.strip())
                    current = line + "\n"
                else:
                    current += line + "\n"
            if current.strip():
                chunks.append(current.strip())
            for chunk in chunks:
                await bot.send_message(chat_id=admin_chat_id, text=chunk)

        # Log
        duration = int((time.monotonic() - start) * 1000)
        await supabase.table("brain_sync_log").insert({
            "task_name": "morning_brief",
            "status": "success",
            "duration_ms": duration,
        }).execute()
        logger.info(f"morning_brief sent in {duration}ms")

    except Exception as e:
        logger.error(f"morning_brief failed: {e}")
        error_tracker.track_exception("morning_brief.main", e)
        try:
            await supabase.table("brain_sync_log").insert({
                "task_name": "morning_brief",
                "status": "failed",
                "details": str(e)[:500],
            }).execute()
        except Exception:
            pass
