"""session_compress — 2AM checkpoint writer for context recovery.

Includes entropy scoring: measures session staleness across 5 signals.
Score >= 2 triggers checkpoint warning. Score >= 4 recommends restart.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("wingmen.nervous_system.session_compress")

CHECKPOINT_PATH = Path(os.path.expanduser("~/wingmen/SESSION_CHECKPOINT.md"))


def _compute_entropy_score(last_sync: dict | None, jobs: list[dict], repos: list[dict] | None) -> tuple[int, list[str]]:
    """Compute 5-point entropy score for session health.

    Returns (score, reasons). Higher = more entropy = needs restart.
    """
    score = 0
    reasons = []

    now = datetime.now(timezone.utc)

    # 1. Brain snapshot staleness (>6 hours)
    if last_sync:
        sync_time_str = last_sync.get("created_at") or last_sync.get("snapshot_at")
        if sync_time_str:
            try:
                sync_time = datetime.fromisoformat(sync_time_str.replace("Z", "+00:00"))
                hours_old = (now - sync_time).total_seconds() / 3600
                if hours_old > 6:
                    score += 1
                    reasons.append(f"Brain snapshot is {hours_old:.0f}h old (>6h)")
            except (ValueError, TypeError):
                score += 1
                reasons.append("Cannot parse brain snapshot timestamp")
    else:
        score += 1
        reasons.append("No brain snapshot found")

    # 2. Recent brain_sync degradation
    if last_sync and last_sync.get("sync_health") in ("degraded", "failed"):
        score += 1
        reasons.append(f"Last sync health: {last_sync['sync_health']}")

    # 3. Failed jobs accumulating
    failed_jobs = [j for j in jobs if j.get("status") == "failed"]
    if len(failed_jobs) >= 2:
        score += 1
        reasons.append(f"{len(failed_jobs)} failed jobs in queue")

    # 4. Low-confidence products
    if repos:
        low_conf = [r for r in repos if r.get("confidence") == "low"]
        if low_conf:
            score += 1
            names = ", ".join(r.get("name", "?") for r in low_conf[:3])
            reasons.append(f"Low confidence: {names}")

    # 5. No activity in 72h (session may be stale)
    if repos:
        any_recent = any(r.get("commits_24h", 0) > 0 or r.get("commits_7d", 0) > 3 for r in repos)
        if not any_recent:
            score += 1
            reasons.append("No repos show recent activity")

    return score, reasons


async def session_compress(supabase):
    """Write SESSION_CHECKPOINT.md with entropy scoring for context recovery."""
    start = time.monotonic()
    logger.info("session_compress starting...")

    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Get active jobs (include failed for entropy scoring)
        jobs_result = await supabase.table("jobs").select("id, repo_name, status, description").in_("status", ["running", "queued", "failed"]).execute()
        jobs = jobs_result.data or []
        active_jobs = [j for j in jobs if j["status"] in ("running", "queued")]

        # Get active clients
        clients_result = await supabase.table("clients").select("name, plan, active, telegram_chat_id").eq("active", True).execute()
        clients = clients_result.data or []

        # Get latest brain sync (with repos for entropy scoring)
        brain_result = await supabase.table("wingmen_brain").select("created_at, snapshot_at, sync_health, repos").order("created_at", desc=True).limit(1).execute()
        last_sync = brain_result.data[0] if brain_result.data else None
        repos = last_sync.get("repos", []) if last_sync else None

        # Compute entropy score
        entropy_score, entropy_reasons = _compute_entropy_score(last_sync, jobs, repos)
        if entropy_score >= 4:
            entropy_status = "🔴 RESTART RECOMMENDED"
        elif entropy_score >= 2:
            entropy_status = "🟡 ELEVATED — monitor closely"
        else:
            entropy_status = "🟢 HEALTHY"

        # Build checkpoint
        sync_time = (last_sync.get("created_at") or last_sync.get("snapshot_at")) if last_sync else "Never"
        sync_health = last_sync["sync_health"] if last_sync else "Unknown"

        lines = [
            f"# Session Checkpoint — Auto-Generated",
            f"",
            f"Generated: {now}",
            f"",
            f"## Entropy Score: {entropy_score}/5 — {entropy_status}",
        ]
        if entropy_reasons:
            for reason in entropy_reasons:
                lines.append(f"- {reason}")
        else:
            lines.append("- All signals healthy")
        lines.append("")

        lines.extend([
            f"## Last Brain Sync",
            f"- Time: {sync_time}",
            f"- Health: {sync_health}",
            f"",
            f"## Active Jobs ({len(active_jobs)})",
        ])

        if active_jobs:
            for j in active_jobs:
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
        if entropy_score >= 4:
            lines.append("- ⚠️ HIGH ENTROPY: Consider restarting CTO Bot for fresh context")

        CHECKPOINT_PATH.write_text("\n".join(lines))
        logger.info(f"SESSION_CHECKPOINT.md written to {CHECKPOINT_PATH} (entropy: {entropy_score}/5)")

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
