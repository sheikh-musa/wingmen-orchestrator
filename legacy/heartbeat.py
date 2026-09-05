"""Heartbeat — writes periodic health status to Supabase.

ihsanOS super admin dashboard reads this to show bot status.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger("wingmen.heartbeat")

_start_time = time.time()


async def write_heartbeat(supabase, service: str = "orchestrator", **extra_metadata) -> None:
    """Write/update heartbeat for a service."""
    try:
        metadata = {
            "uptime_seconds": int(time.time() - _start_time),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **extra_metadata,
        }

        await supabase.table("bot_heartbeat").upsert({
            "service": service,
            "status": "healthy",
            "last_ping": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata,
        }, on_conflict="service").execute()

    except Exception as e:
        logger.warning(f"Heartbeat write failed: {e}")


async def write_orchestrator_heartbeat(supabase) -> None:
    """Write orchestrator heartbeat with job + bug stats."""

    # Count active jobs
    try:
        jobs_result = await supabase.table("jobs").select("id", count="exact").eq("status", "running").execute()
        active_jobs = jobs_result.count or 0
    except Exception:
        active_jobs = 0

    # Count pending bugs
    try:
        bugs_result = await supabase.table("bug_reports").select("id", count="exact").in_("status", ["new", "diagnosing", "proposed"]).execute()
        pending_bugs = bugs_result.count or 0
    except Exception:
        pending_bugs = 0

    # Count total bugs today
    try:
        from datetime import date
        today = date.today().isoformat()
        today_result = await supabase.table("bug_reports").select("id", count="exact").gte("created_at", today).execute()
        bugs_today = today_result.count or 0
    except Exception:
        bugs_today = 0

    await write_heartbeat(
        supabase,
        service="orchestrator",
        active_jobs=active_jobs,
        pending_bugs=pending_bugs,
        bugs_today=bugs_today,
    )


async def write_bot_heartbeat(supabase, active_sessions: int = 0) -> None:
    """Write Telegram bot heartbeat."""
    await write_heartbeat(
        supabase,
        service="cto_bot",
        active_sessions=active_sessions,
    )


async def write_client_bot_heartbeat(
    supabase,
    client_id: int,
    bot_username: str,
    messages_count: int = 0,
    active_conversations: int = 0,
) -> None:
    """Write heartbeat for a specific client bot."""
    await write_heartbeat(
        supabase,
        service=f"bot_{bot_username}",
        messages_count=messages_count,
        active_conversations=active_conversations,
        client_id=client_id,
    )
