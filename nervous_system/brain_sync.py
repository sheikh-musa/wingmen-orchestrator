"""brain_sync — scan all active repos, write snapshot to Supabase, generate BRAIN.md."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("wingmen.nervous_system.brain_sync")

REPOS_JSON = Path(__file__).parent.parent / "REPOS.json"
BRAIN_MD_PATH = Path(os.path.expanduser("~/wingmen/BRAIN.md"))


def _load_active_repos() -> list[dict]:
    """Load repos with status 'active' from REPOS.json."""
    with open(REPOS_JSON) as f:
        data = json.load(f)
    return [r for r in data.get("repos", []) if r.get("status") == "active"]


async def _scan_repo(repo: dict) -> dict:
    """Scan a single repo and return its state."""
    name = repo["name"]
    local_path = os.path.expanduser(repo.get("local_path", ""))
    deploy_url = repo.get("deploy_url", "")

    state = {
        "name": name,
        "status": repo.get("status", "unknown"),
        "deploy_url": deploy_url,
        "health": "green",
        "last_commit": "",
        "last_commit_at": "",
        "commits_24h": 0,
        "commits_7d": 0,
        "status_md_found": False,
        "status_md_phase": "",
        "blockers": [],
        "next_milestones": [],
        "revenue_signals": [],
        "contradictions": [],
        "scan_succeeded": False,
        "scan_error": None,
    }

    if not os.path.isdir(local_path):
        state["scan_error"] = f"Local path not found: {local_path}"
        state["health"] = "red"
        return state

    try:
        # Last commit
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", local_path, "log", "--oneline", "-1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        last = stdout.decode().strip()
        if last:
            parts = last.split(" ", 1)
            state["last_commit"] = parts[1] if len(parts) > 1 else last

        # Last commit timestamp
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", local_path, "log", "-1", "--format=%aI",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        state["last_commit_at"] = stdout.decode().strip()

        # Commits in last 24h
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", local_path, "rev-list", "--count", "--since=24 hours ago", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        state["commits_24h"] = int(stdout.decode().strip() or "0")

        # Commits in last 7d
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", local_path, "rev-list", "--count", "--since=7 days ago", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        state["commits_7d"] = int(stdout.decode().strip() or "0")

        # Parse STATUS.md
        status_path = Path(local_path) / "STATUS.md"
        if status_path.exists():
            state["status_md_found"] = True
            content = status_path.read_text()
            for line in content.split("\n"):
                line_lower = line.strip().lower()
                if line_lower.startswith("phase:"):
                    state["status_md_phase"] = line.split(":", 1)[1].strip()
                elif line_lower.startswith("health:"):
                    h = line.split(":", 1)[1].strip().lower()
                    if h in ("green", "yellow", "red"):
                        state["health"] = h
            # Extract blockers
            in_blocked = False
            for line in content.split("\n"):
                if line.strip().lower().startswith("## blocked"):
                    in_blocked = True
                    continue
                if in_blocked and line.startswith("## "):
                    break
                if in_blocked and line.strip().startswith("- "):
                    state["blockers"].append(line.strip()[2:])

        # Detect contradictions
        if state["commits_7d"] == 0 and state["status_md_phase"] and "deploy" in state["status_md_phase"].lower():
            state["contradictions"].append("STATUS.md says deployed but no commits in 7 days — may be stale")
            state["health"] = "yellow"

        if not state["status_md_found"] and state["commits_24h"] > 0:
            state["contradictions"].append("Active commits but no STATUS.md — add one for visibility")

        state["scan_succeeded"] = True

    except asyncio.TimeoutError:
        state["scan_error"] = "Git command timed out"
        state["health"] = "red"
    except Exception as e:
        state["scan_error"] = str(e)[:200]
        state["health"] = "red"

    return state


def _generate_brain_md(repo_states: list[dict], jobs: list[dict], clients: list[dict]) -> str:
    """Generate human-readable BRAIN.md from snapshot data."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Wingmen Brain — Auto-Generated\n", f"Last sync: {now}\n"]

    health_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}

    lines.append("## Repos\n")
    for r in repo_states:
        icon = health_icon.get(r["health"], "⚪")
        commit_info = f" — {r['commits_24h']} commits today" if r["commits_24h"] > 0 else ""
        lines.append(f"{icon} **{r['name']}**{commit_info}")
        if r["blockers"]:
            for b in r["blockers"]:
                lines.append(f"   Blocked: {b}")
        if r["contradictions"]:
            for c in r["contradictions"]:
                lines.append(f"   ⚠️ {c}")
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
        failed_scans = [r for r in repo_states if not r["scan_succeeded"]]
        sync_health = "healthy" if not failed_scans else "degraded" if len(failed_scans) < len(repo_states) else "failed"

        # 6. Write snapshot to Supabase
        snapshot = {
            "repos": [dict(r) for r in repo_states],
            "job_queue": jobs,
            "clients": clients,
            "sync_health": sync_health,
        }
        await supabase.table("wingmen_brain").insert(snapshot).execute()

        # 7. Generate BRAIN.md
        brain_md = _generate_brain_md(list(repo_states), jobs, clients)
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
