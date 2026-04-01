"""brain_sync — scan all active repos, write snapshot to Supabase, generate BRAIN.md.

Self-improvement layer: confidence scoring, contradiction detection,
regression alerts, consecutive failure alerting, context notes synthesis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("wingmen.nervous_system.brain_sync")

REPOS_JSON = Path(__file__).parent.parent / "REPOS.json"
BRAIN_MD_PATH = Path(os.path.expanduser("~/wingmen/BRAIN.md"))
CONSECUTIVE_FAILURE_THRESHOLD = 3


@dataclass
class RepoState:
    """Typed state for a single repo scan — no bare dicts."""
    name: str
    status: str = "unknown"
    deploy_url: str = ""
    health: str = "green"
    last_commit: str = ""
    last_commit_sha: str = ""
    last_commit_at: str = ""
    commits_24h: int = 0
    commits_7d: int = 0
    status_md_found: bool = False
    status_md_phase: str = ""
    blockers: list[str] = field(default_factory=list)
    next_milestones: list[str] = field(default_factory=list)
    revenue_signals: list[str] = field(default_factory=list)
    cto_questions: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    confidence: str = "low"
    confidence_reason: str = ""
    scan_succeeded: bool = False
    scan_error: str | None = None


def _load_active_repos() -> list[dict]:
    """Load repos with status 'active' from REPOS.json."""
    with open(REPOS_JSON) as f:
        data = json.load(f)
    return [r for r in data.get("repos", []) if r.get("status") == "active"]


async def _scan_repo(repo: dict) -> RepoState:
    """Scan a single repo and return its state."""
    name = repo["name"]
    local_path = os.path.expanduser(repo.get("local_path", ""))
    deploy_url = repo.get("deploy_url", "")

    state = RepoState(
        name=name,
        status=repo.get("status", "unknown"),
        deploy_url=deploy_url,
    )

    if not os.path.isdir(local_path):
        state.scan_error = f"Local path not found: {local_path}"
        state.health = "red"
        return state

    try:
        # Last commit (message + SHA)
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", local_path, "log", "-1", "--format=%H %s",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        last = stdout.decode().strip()
        if last:
            parts = last.split(" ", 1)
            state.last_commit_sha = parts[0] if parts else ""
            state.last_commit = parts[1] if len(parts) > 1 else last

        # Last commit timestamp
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", local_path, "log", "-1", "--format=%aI",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        state.last_commit_at = stdout.decode().strip()

        # Commits in last 24h
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", local_path, "rev-list", "--count", "--since=24 hours ago", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        state.commits_24h = int(stdout.decode().strip() or "0")

        # Commits in last 7d
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", local_path, "rev-list", "--count", "--since=7 days ago", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        state.commits_7d = int(stdout.decode().strip() or "0")

        # Parse STATUS.md — extract all sections
        status_path = Path(local_path) / "STATUS.md"
        if status_path.exists():
            state.status_md_found = True
            content = status_path.read_text()

            # Header fields (Phase, Health)
            for line in content.split("\n"):
                line_lower = line.strip().lower()
                if line_lower.startswith("phase:"):
                    state.status_md_phase = line.split(":", 1)[1].strip()
                elif line_lower.startswith("health:"):
                    h = line.split(":", 1)[1].strip().lower()
                    if h in ("green", "yellow", "red"):
                        state.health = h

            # Section parser — extracts bullet items under ## headings
            section_map = {
                "blocked": state.blockers,
                "next up": state.next_milestones,
                "next": state.next_milestones,
                "revenue signals": state.revenue_signals,
                "revenue": state.revenue_signals,
                "questions for cto": state.cto_questions,
                "questions": state.cto_questions,
            }
            current_section: list[str] | None = None
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.lower().startswith("## "):
                    heading = stripped[3:].strip().lower()
                    current_section = None
                    for key, target in section_map.items():
                        if heading.startswith(key):
                            current_section = target
                            break
                elif current_section is not None and stripped.startswith("- "):
                    current_section.append(stripped[2:])

        # Detect contradictions
        if state.commits_7d == 0 and state.status_md_phase and "deploy" in state.status_md_phase.lower():
            state.contradictions.append("STATUS.md says deployed but no commits in 7 days — may be stale")
            state.health = "yellow"

        if not state.status_md_found and state.commits_24h > 0:
            state.contradictions.append("Active commits but no STATUS.md — add one for visibility")

        # Confidence scoring based on data quality
        n_contradictions = len(state.contradictions)
        if n_contradictions == 0 and state.status_md_found:
            state.confidence = "high"
            state.confidence_reason = "No contradictions, STATUS.md present"
        elif n_contradictions <= 1:
            state.confidence = "medium"
            state.confidence_reason = f"{n_contradictions} contradiction(s) found" if n_contradictions else "No STATUS.md"
        else:
            state.confidence = "low"
            state.confidence_reason = f"{n_contradictions} contradictions detected"

        state.scan_succeeded = True

    except asyncio.TimeoutError:
        state.scan_error = "Git command timed out"
        state.health = "red"
    except Exception as e:
        state.scan_error = str(e)[:200]
        state.health = "red"

    return state


def _generate_brain_md(repo_states: list[RepoState], jobs: list[dict], clients: list[dict], context_notes: list[str] | None = None) -> str:
    """Generate human-readable BRAIN.md from snapshot data."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Wingmen Brain — Auto-Generated\n", f"Last sync: {now}\n"]

    health_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}

    # Context notes (regression alerts) at the top
    if context_notes:
        lines.append("## Alerts & Changes\n")
        for note in context_notes:
            lines.append(f"- {note}")
        lines.append("")

    lines.append("## Repos\n")
    for r in repo_states:
        icon = health_icon.get(r.health, "⚪")
        conf = conf_icon.get(r.confidence, "⚪")
        commit_info = f" — {r.commits_24h} commits today" if r.commits_24h > 0 else ""
        lines.append(f"{icon} **{r.name}** {conf} confidence{commit_info}")
        if r.confidence_reason:
            lines.append(f"   Confidence: {r.confidence} — {r.confidence_reason}")
        if r.blockers:
            for b in r.blockers:
                lines.append(f"   Blocked: {b}")
        if r.contradictions:
            for c in r.contradictions:
                lines.append(f"   ⚠️ {c}")
        if r.next_milestones:
            lines.append(f"   Next: {', '.join(r.next_milestones[:3])}")
        if r.revenue_signals:
            lines.append(f"   💰 {', '.join(r.revenue_signals[:3])}")
        if r.cto_questions:
            for q in r.cto_questions:
                lines.append(f"   ❓ CTO Q: {q}")
        lines.append("")

    # CTO Questions summary (surfaced at top level for visibility)
    all_questions = [(r.name, q) for r in repo_states for q in r.cto_questions]
    if all_questions:
        lines.append("## Questions for CTO\n")
        for repo_name, q in all_questions:
            lines.append(f"- [{repo_name}] {q}")
        lines.append("")

    if jobs:
        lines.append("## Active Jobs\n")
        for j in jobs:
            lines.append(f"- #{j['id']} {j['repo_name']} — {j['status']}: {j.get('description', '')[:80]}")
        lines.append("")

    if clients:
        lines.append("## Clients\n")
        for c in clients:
            lines.append(f"- {c['name']} ({c['plan']}) — {'active' if c['active'] else 'inactive'}")
        lines.append("")

    return "\n".join(lines)


async def _check_consecutive_failures(supabase, bot=None, admin_chat_id: str = ""):
    """Check if last N brain_sync runs all failed/degraded. Alert if so."""
    try:
        result = await supabase.table("brain_sync_log").select("status, details").eq("task_name", "brain_sync").order("created_at", desc=True).limit(CONSECUTIVE_FAILURE_THRESHOLD).execute()
        recent = result.data or []
        if len(recent) >= CONSECUTIVE_FAILURE_THRESHOLD:
            all_bad = all(r.get("status") in ("failed", "degraded") for r in recent)
            if all_bad:
                msg = f"🚨 ALERT: {CONSECUTIVE_FAILURE_THRESHOLD} consecutive brain_sync runs failed or degraded. Investigate Mac Mini immediately."
                logger.critical(msg)
                if bot and admin_chat_id:
                    try:
                        await bot.send_message(chat_id=admin_chat_id, text=msg)
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"Consecutive failure check failed (non-fatal): {e}")


async def brain_sync(supabase):
    """Main brain_sync task — called by scheduler."""
    start = time.monotonic()
    logger.info("brain_sync starting...")

    try:
        # 1. Load active repos
        repos = _load_active_repos()
        logger.info(f"Scanning {len(repos)} active repos")

        # 2. Scan repos in parallel (max 4 concurrent)
        sem = asyncio.Semaphore(4)

        async def scan_with_sem(repo):
            async with sem:
                return await _scan_repo(repo)

        repo_states = await asyncio.gather(*[scan_with_sem(r) for r in repos])

        # 3. Get active jobs
        jobs_result = await supabase.table("jobs").select("id, repo_name, status, description").in_("status", ["running", "queued"]).execute()
        jobs = jobs_result.data or []

        # 4. Get active clients
        clients_result = await supabase.table("clients").select("name, plan, active").eq("active", True).execute()
        clients = clients_result.data or []

        # 5. Determine sync health
        failed_scans = [r for r in repo_states if not r.scan_succeeded]
        sync_health = "healthy" if not failed_scans else "degraded" if len(failed_scans) < len(repo_states) else "failed"

        # 5b. Regression detection — compare to previous snapshot
        context_notes = []
        try:
            prev_result = await supabase.table("wingmen_brain").select("repos, sync_health").order("created_at", desc=True).limit(1).execute()
            if prev_result.data:
                prev_repos = {r["name"]: r for r in prev_result.data[0].get("repos", [])}
                for r in repo_states:
                    prev = prev_repos.get(r.name)
                    if not prev:
                        context_notes.append(f"📋 New repo tracked: {r.name}")
                        continue
                    # Health regression
                    if prev.get("health") != r.health:
                        if r.health in ("red", "yellow") and prev.get("health") == "green":
                            context_notes.append(f"⚠️ {r.name} health DEGRADED: {prev['health']} → {r.health}")
                        elif r.health == "green" and prev.get("health") in ("red", "yellow"):
                            context_notes.append(f"✅ {r.name} RECOVERED: {prev['health']} → green")
                    # New blockers
                    old_blockers = set(prev.get("blockers", []))
                    new_blockers = set(r.blockers)
                    for b in new_blockers - old_blockers:
                        context_notes.append(f"🚧 {r.name}: NEW BLOCKER — {b}")
                    for b in old_blockers - new_blockers:
                        context_notes.append(f"✅ {r.name}: blocker RESOLVED — {b}")
                    # Confidence drop
                    conf_order = {"high": 3, "medium": 2, "low": 1}
                    if conf_order.get(r.confidence, 0) < conf_order.get(prev.get("confidence"), 0):
                        context_notes.append(f"📉 {r.name}: confidence dropped {prev.get('confidence')} → {r.confidence} — {r.confidence_reason}")
                # Sync health regression
                prev_sync = prev_result.data[0].get("sync_health", "healthy")
                if sync_health != prev_sync and sync_health in ("degraded", "failed"):
                    context_notes.append(f"⚠️ Sync health DEGRADED: {prev_sync} → {sync_health}")
        except Exception as e:
            logger.warning(f"Regression detection failed (non-fatal): {e}")

        # 6. Write snapshot to Supabase (serialize dataclasses to dicts)
        snapshot = {
            "repos": [asdict(r) for r in repo_states],
            "job_queue": jobs,
            "clients": clients,
            "sync_health": sync_health,
            "context_notes": "\n".join(context_notes) if context_notes else None,
        }
        await supabase.table("wingmen_brain").insert(snapshot).execute()

        # 7. Generate BRAIN.md
        brain_md = _generate_brain_md(list(repo_states), jobs, clients, context_notes)
        BRAIN_MD_PATH.write_text(brain_md)
        logger.info(f"BRAIN.md written to {BRAIN_MD_PATH}")

        # 8. Cleanup old snapshots (>30 days)
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        await supabase.table("wingmen_brain").delete().lt("created_at", cutoff).execute()

        # 9. Log success
        duration = int((time.monotonic() - start) * 1000)
        await supabase.table("brain_sync_log").insert({
            "task_name": "brain_sync",
            "status": "success",
            "duration_ms": duration,
            "details": f"Scanned {len(repos)} repos, {len(failed_scans)} failed",
        }).execute()

        logger.info(f"brain_sync complete in {duration}ms — {sync_health}")

        # 10. Consecutive failure alerting
        await _check_consecutive_failures(supabase)

    except Exception as e:
        logger.error(f"brain_sync failed: {e}")
        duration = int((time.monotonic() - start) * 1000)
        try:
            await supabase.table("brain_sync_log").insert({
                "task_name": "brain_sync",
                "status": "failed",
                "duration_ms": duration,
                "details": str(e)[:500],
            }).execute()
        except Exception:
            pass
