"""weekly_digest — Sunday 9PM strategic weekly review."""

from __future__ import annotations

import logging
import os
import sys
import time

# Ensure parent dir is on path for ai_provider import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_provider import call_ai

logger = logging.getLogger("wingmen.nervous_system.weekly_digest")

CTO_PRINCIPLES_PATH = os.path.join(os.path.dirname(__file__), "..", "CTO_PRINCIPLES.md")


async def weekly_digest(supabase, bot, admin_chat_id: str):
    """Send Sunday 9PM weekly strategic review."""
    start = time.monotonic()
    logger.info("weekly_digest starting...")

    try:
        # Get last 7 days of snapshots
        from datetime import datetime, timezone, timedelta
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        result = await supabase.table("wingmen_brain").select("*").gte("snapshot_at", week_ago).order("snapshot_at", desc=True).execute()
        snapshots = result.data or []

        if not snapshots:
            logger.warning("No snapshots this week — skipping digest")
            return

        latest = snapshots[0]
        earliest = snapshots[-1] if len(snapshots) > 1 else snapshots[0]

        # Build week summary data
        repos_latest = {r["name"]: r for r in latest.get("repos", [])}
        repos_earliest = {r["name"]: r for r in earliest.get("repos", [])}

        summary_lines = []
        for name, r in repos_latest.items():
            commits = r.get("commits_7d", 0)
            health = r.get("health", "unknown")
            old_health = repos_earliest.get(name, {}).get("health", "unknown")
            change = ""
            if old_health != health:
                change = f" (was {old_health})"
            summary_lines.append(f"- {name}: {commits} commits, health={health}{change}, blockers={len(r.get('blockers', []))}")

        summary = "\n".join(summary_lines)

        # Load principles
        principles = ""
        if os.path.exists(CTO_PRINCIPLES_PATH):
            with open(CTO_PRINCIPLES_PATH) as f:
                principles = f.read()

        prompt = f"""You are Musa's CTO doing the weekly strategic review (Sunday evening).

{principles}

This week's project activity:
{summary}

Active clients: {len(latest.get('clients', []))}
Active jobs: {len(latest.get('job_queue', []))}
Total snapshots this week: {len(snapshots)}

Write a concise weekly digest covering:
1. What shipped this week (2-3 highlights)
2. Client health (who's active, who needs attention)
3. What should be the #1 focus next week and why
4. Barakah check: any open-source/community contributions this week?

Keep it under 300 words. Be direct."""

        try:
            digest = await call_ai(prompt, model="fast", max_tokens=1024)
        except Exception as e:
            logger.error(f"Weekly digest AI call failed: {e}")
            digest = "(Weekly analysis unavailable)"

        now = datetime.now(timezone.utc)
        message = f"\U0001f4ca Wingmen Weekly Digest \u2014 Week of {now.strftime('%d %b %Y')}\n\n{digest}"

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

        duration = int((time.monotonic() - start) * 1000)
        await supabase.table("brain_sync_log").insert({
            "task_name": "weekly_digest",
            "status": "success",
            "duration_ms": duration,
        }).execute()
        logger.info(f"weekly_digest sent in {duration}ms")

    except Exception as e:
        logger.error(f"weekly_digest failed: {e}")
        try:
            await supabase.table("brain_sync_log").insert({
                "task_name": "weekly_digest", "status": "failed", "details": str(e)[:500],
            }).execute()
        except Exception:
            pass
