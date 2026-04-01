"""morning_brief — 6AM strategic Telegram brief with CTO recommendations."""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("wingmen.nervous_system.morning_brief")

CTO_PRINCIPLES_PATH = os.path.join(os.path.dirname(__file__), "..", "CTO_PRINCIPLES.md")
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
CLAUDE_ENV = {
    "HOME": os.path.expanduser("~"),
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "USER": os.environ.get("USER", ""),
    "SHELL": os.environ.get("SHELL", ""),
    "LANG": os.environ.get("LANG", ""),
}


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
    """Ask Claude for strategic CTO recommendations."""
    import asyncio

    principles = ""
    if os.path.exists(CTO_PRINCIPLES_PATH):
        with open(CTO_PRINCIPLES_PATH) as f:
            principles = f.read()

    prompt = f"""You are Musa's CTO — a Muslim technologist who thinks strategically about both business growth and community impact.

{principles}

Current state of all projects:
{status_summary}

Based on this:
1. What should Musa focus on today? (highest impact action)
2. What's at risk if nothing changes? (blockers aging, clients quiet)
3. What opportunity is being missed?

Keep it to 3-5 bullet points. Be decisive, not diplomatic. Start each with an action verb."""

    proc = await asyncio.create_subprocess_exec(
        CLAUDE_BIN, "-p", prompt, "--output-format", "text",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=CLAUDE_ENV,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        return stdout.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "(Strategic analysis timed out)"


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

        # Part 1: Status overview
        status = _build_status_summary(snapshot)

        # Part 2: Strategic analysis
        analysis = await _get_strategic_analysis(status)

        # Compose message
        date_str = now.strftime("%d %b %Y")
        message = f"\u2600\ufe0f Wingmen Morning Brief — {date_str}\n\n{status}\n\n\U0001f4a1 CTO Recommendations:\n{analysis}"

        # Send via Telegram
        await bot.send_message(chat_id=admin_chat_id, text=message[:4000])

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
        try:
            await supabase.table("brain_sync_log").insert({
                "task_name": "morning_brief",
                "status": "failed",
                "details": str(e)[:500],
            }).execute()
        except Exception:
            pass
