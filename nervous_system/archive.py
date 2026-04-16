"""Archive terminal-state jobs and decisions — keep the governance queue readable.

TASK-031: Runs once daily at 03:00 SGT (called from wingmen_orch.py).

Job archiving:
  - Moves jobs WHERE status IN (completed, failed, paused)
    AND updated_at < now() - 3 days into jobs_archive.
  - Deletes associated build_log rows first (FK constraint).
  - Purges jobs_archive rows older than 90 days (execution history,
    not durable record).

Decision archiving:
  - Moves strategic_decisions WHERE challenge_status IN
    (implemented, blocked, overridden) AND created_at < now() - 14 days
    into strategic_decisions_archive.
  - Keeps: accepted, challenge_window, cai_review_requested.
  - Purges strategic_decisions_archive rows older than 365 days
    (institutional memory — must persist).

All activity is logged to build_log (phase=archive) so the governance
dashboard shows archive runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("wingmen.archive")

JOB_ARCHIVE_AFTER_DAYS = 3
DECISION_ARCHIVE_AFTER_DAYS = 14
JOB_ARCHIVE_RETAIN_DAYS = 90
DECISION_ARCHIVE_RETAIN_DAYS = 365

# challenge_status values that indicate a terminal decision
_TERMINAL_DECISION_STATUSES = ["implemented", "blocked", "overridden"]
# challenge_status values to keep live (active review states)
_LIVE_DECISION_STATUSES = ("accepted", "challenge_window", "cai_review_requested")


async def run_archive(supabase) -> None:
    """Main entry point — called from the wingmen_orch.py main loop."""
    now = datetime.now(timezone.utc)
    job_cutoff = (now - timedelta(days=JOB_ARCHIVE_AFTER_DAYS)).isoformat()
    decision_cutoff = (now - timedelta(days=DECISION_ARCHIVE_AFTER_DAYS)).isoformat()

    archived_jobs = await _archive_jobs(supabase, job_cutoff)
    archived_decisions = await _archive_decisions(supabase, decision_cutoff)
    purged_jobs = await _purge_old_archive(supabase, "jobs_archive", JOB_ARCHIVE_RETAIN_DAYS, now)
    purged_decisions = await _purge_old_archive(
        supabase, "strategic_decisions_archive", DECISION_ARCHIVE_RETAIN_DAYS, now
    )

    summary = (
        f"archive: +{archived_jobs} jobs, +{archived_decisions} decisions archived; "
        f"-{purged_jobs} jobs, -{purged_decisions} decisions purged"
    )
    logger.info(summary)

    try:
        await supabase.table("build_log").insert({
            "job_id": None,
            "repo_name": "orchestrator",
            "phase": "archive",
            "message": summary,
            "level": "info",
        }).execute()
    except Exception as e:
        logger.warning(f"Archive: failed to write build_log: {e}")


async def _archive_jobs(supabase, cutoff: str) -> int:
    result = await (
        supabase.table("jobs")
        .select(
            "id, repo_name, description, status, priority, fail_count, "
            "session_prompt, result_summary, triggered_by, created_at, updated_at"
        )
        .in_("status", ["completed", "failed", "paused"])
        .lt("updated_at", cutoff)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return 0

    ids = [row["id"] for row in rows]
    archive_rows = [
        {**row, "archived_at": datetime.now(timezone.utc).isoformat()}
        for row in rows
    ]

    try:
        # Delete build_log entries first to satisfy FK constraint
        await supabase.table("build_log").delete().in_("job_id", ids).execute()
        await supabase.table("jobs_archive").upsert(archive_rows, on_conflict="id").execute()
        await supabase.table("jobs").delete().in_("id", ids).execute()
        logger.info(
            f"Archived {len(ids)} jobs: "
            f"{ids[:10]}{'...' if len(ids) > 10 else ''}"
        )
        return len(ids)
    except Exception as e:
        logger.error(f"Archive jobs failed: {e}")
        return 0


async def _archive_decisions(supabase, cutoff: str) -> int:
    result = await (
        supabase.table("strategic_decisions")
        .select(
            "id, decision_ref, title, decision, reasoning, repos_affected, source, "
            "challenge_status, bypass_review, notified_at, execution_status, "
            "completed_job_id, completed_at, category, parent_ref, created_at"
        )
        .in_("challenge_status", _TERMINAL_DECISION_STATUSES)
        .lt("created_at", cutoff)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return 0

    ids = [row["id"] for row in rows]
    archive_rows = [
        {**row, "archived_at": datetime.now(timezone.utc).isoformat()}
        for row in rows
    ]

    try:
        await supabase.table("strategic_decisions_archive").upsert(
            archive_rows, on_conflict="id"
        ).execute()
        await supabase.table("strategic_decisions").delete().in_("id", ids).execute()
        logger.info(
            f"Archived {len(ids)} decisions: "
            f"{[r['decision_ref'] for r in rows[:10]]}"
        )
        return len(ids)
    except Exception as e:
        logger.error(f"Archive decisions failed: {e}")
        return 0


async def _purge_old_archive(
    supabase, table: str, retain_days: int, now: datetime
) -> int:
    retain_cutoff = (now - timedelta(days=retain_days)).isoformat()
    try:
        result = await (
            supabase.table(table)
            .delete()
            .lt("archived_at", retain_cutoff)
            .execute()
        )
        count = len(result.data or [])
        if count:
            logger.info(f"Purged {count} rows from {table} (>{retain_days}d old)")
        return count
    except Exception as e:
        logger.error(f"Purge {table} failed: {e}")
        return 0
