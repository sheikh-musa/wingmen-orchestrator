"""session_compress — 2AM checkpoint writer for context recovery."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("wingmen.nervous_system.session_compress")

CHECKPOINT_PATH = Path(os.path.expanduser("~/wingmen/SESSION_CHECKPOINT.md"))


async def session_compress(supabase):
    """Write SESSION_CHECKPOINT.md for context recovery after restarts."""
    start = time.monotonic()
    logger.info("session_compress starting...")

    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Get active jobs
        jobs_result = await supabase.table("jobs").select("id, repo_name, status, description").in_("status", ["running", "queued"]).execute()
        jobs = jobs_result.data or []

        # Get active clients
        clients_result = await supabase.table("clients").select("name, plan, active, telegram_chat_id").eq("active", True).execute()
        clients = clients_result.data or []

        # Get latest brain sync
        brain_result = await supabase.table("wingmen_brain").select("snapshot_at, sync_health").order("snapshot_at", desc=True).limit(1).execute()
        last_sync = brain_result.data[0] if brain_result.data else None

        # Build checkpoint
        lines = [
            f"# Session Checkpoint — Auto-Generated",
            f"",
            f"Generated: {now}",
            f"",
            f"## Last Brain Sync",
            f"- Time: {last_sync['snapshot_at'] if last_sync else 'Never'}",
            f"- Health: {last_sync['sync_health'] if last_sync else 'Unknown'}",
            f"",
            f"## Active Jobs ({len(jobs)})",
        ]

        if jobs:
            for j in jobs:
                desc = (j.get("description") or "")[:80]
                lines.append(f"- #{j['id']} [{j['status']}] {j['repo_name']}: {desc}")
        else:
            lines.append("- None")

        lines.extend([
            f"",
            f"## Active Clients ({len(clients)})",
        ])

        if clients:
            for c in clients:
                linked = "linked" if c.get("telegram_chat_id") else "not linked"
                lines.append(f"- {c['name']} ({c['plan']}) — Telegram {linked}")
        else:
            lines.append("- None")

        lines.extend(["", f"## Notes", "- Context recovery file for CTO Bot restart", "- Read this file to restore awareness of current state"])

        CHECKPOINT_PATH.write_text("\n".join(lines))
        logger.info(f"SESSION_CHECKPOINT.md written to {CHECKPOINT_PATH}")

        # Log
        duration = int((time.monotonic() - start) * 1000)
        await supabase.table("brain_sync_log").insert({
            "task_name": "session_compress",
            "status": "success",
            "duration_ms": duration,
        }).execute()

    except Exception as e:
        logger.error(f"session_compress failed: {e}")
        try:
            await supabase.table("brain_sync_log").insert({
                "task_name": "session_compress", "status": "failed", "details": str(e)[:500],
            }).execute()
        except Exception:
            pass
