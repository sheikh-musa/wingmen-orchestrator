# Nervous System Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 proactive scheduled tasks to the CTO Bot — brain_sync, morning_brief, weekly_digest, memory_sync, session_compress — turning it from a reactive tool into a strategic CTO that monitors all repos, sends morning briefs, and makes recommendations guided by Islamic business principles.

**Architecture:** APScheduler runs inside the existing cto_bot process. Each scheduled task is a standalone async function in the `nervous_system/` module. They share two Supabase tables (`wingmen_brain`, `brain_sync_log`) and read repos from `REPOS.json`. A `CTO_PRINCIPLES.md` file is loaded into the brainstorm agent's admin prompt.

**Tech Stack:** Python 3.9, APScheduler, asyncio, Supabase (postgrest), Claude CLI

**Spec:** `docs/superpowers/specs/2026-04-01-nervous-system-merge-design.md`

**Parallelism:** Tasks 2-6 are fully independent — build them all simultaneously. Task 1 (tables) must complete first. Task 7 (wiring) must come last. Task 8 (CTO principles) is independent of everything.

```
Task 1: Supabase tables ──┐
                           ├── Task 2: brain_sync      ──┐
                           ├── Task 3: morning_brief    ──┤
                           ├── Task 4: weekly_digest    ──├── Task 7: Wire into cto_bot → Task 9: Test & restart
                           ├── Task 5: memory_sync      ──┤
                           └── Task 6: session_compress ──┘
Task 8: CTO principles (independent) ─────────────────────┘
```

---

## File Structure

```
nervous_system/
  __init__.py              — exports all task functions
  brain_sync.py            — scan repos, write snapshot, generate BRAIN.md
  morning_brief.py         — 6AM strategic Telegram brief
  weekly_digest.py         — Sunday 9PM weekly review
  memory_sync.py           — midnight snapshot differ
  session_compress.py      — 2AM checkpoint writer
CTO_PRINCIPLES.md          — Islamic CTO decision framework
cto_bot.py                 — Modified: add scheduler in main()
agents/brainstorm.py       — Modified: load CTO principles for admin
tests/test_nervous_system.py — Tests for all tasks
```

---

### Task 1: Create Supabase tables

**Files:** None (SQL only via Supabase MCP)

- [ ] **Step 1: Create wingmen_brain table**

Run via Supabase MCP (`project_id: tscuymavysscrvoberrr`):

```sql
CREATE TABLE IF NOT EXISTS wingmen_brain (
    id BIGSERIAL PRIMARY KEY,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    repos JSONB NOT NULL,
    job_queue JSONB,
    clients JSONB,
    sync_health TEXT NOT NULL DEFAULT 'healthy',
    context_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wingmen_brain_snapshot_at ON wingmen_brain(snapshot_at DESC);
```

- [ ] **Step 2: Create brain_sync_log table**

```sql
CREATE TABLE IF NOT EXISTS brain_sync_log (
    id BIGSERIAL PRIMARY KEY,
    task_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    duration_ms INTEGER,
    details TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 3: Verify tables exist**

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name IN ('wingmen_brain', 'brain_sync_log');
```

Expected: 2 rows.

---

### Task 2: brain_sync — repo scanner + snapshot writer

**Files:**
- Create: `nervous_system/__init__.py`
- Create: `nervous_system/brain_sync.py`
- Test: `tests/test_nervous_system.py`

- [ ] **Step 1: Create `nervous_system/__init__.py`**

```python
"""Wingmen Nervous System — proactive scheduled tasks."""
```

- [ ] **Step 2: Create `nervous_system/brain_sync.py`**

```python
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
```

- [ ] **Step 3: Write tests in `tests/test_nervous_system.py`**

```python
"""Tests for nervous system scheduled tasks."""

from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import json

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.brain_sync import _load_active_repos, _scan_repo, _generate_brain_md


def test_load_active_repos(tmp_path):
    repos_file = tmp_path / "REPOS.json"
    repos_file.write_text(json.dumps({"repos": [
        {"name": "active-repo", "status": "active", "local_path": "/tmp"},
        {"name": "specced-repo", "status": "specced", "local_path": "/tmp"},
    ]}))
    with patch("nervous_system.brain_sync.REPOS_JSON", repos_file):
        result = _load_active_repos()
        assert len(result) == 1
        assert result[0]["name"] == "active-repo"


@pytest.mark.asyncio
async def test_scan_repo_missing_path():
    repo = {"name": "ghost", "local_path": "/nonexistent/path", "status": "active"}
    state = await _scan_repo(repo)
    assert state["scan_succeeded"] is False
    assert state["health"] == "red"
    assert "not found" in state["scan_error"]


def test_generate_brain_md():
    repos = [
        {"name": "test-repo", "health": "green", "commits_24h": 3, "blockers": [], "contradictions": []},
    ]
    md = _generate_brain_md(repos, [], [])
    assert "test-repo" in md
    assert "🟢" in md
    assert "3 commits today" in md


def test_generate_brain_md_with_blockers():
    repos = [
        {"name": "blocked-repo", "health": "yellow", "commits_24h": 0, "blockers": ["Waiting on API key"], "contradictions": ["Stale STATUS.md"]},
    ]
    md = _generate_brain_md(repos, [], [])
    assert "Blocked: Waiting on API key" in md
    assert "⚠️ Stale STATUS.md" in md
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_nervous_system.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add nervous_system/ tests/test_nervous_system.py
git commit -m "feat: add brain_sync — repo scanner, snapshot writer, BRAIN.md generator"
```

---

### Task 3: morning_brief — 6 AM strategic Telegram brief

**Files:**
- Create: `nervous_system/morning_brief.py`

- [ ] **Step 1: Create `nervous_system/morning_brief.py`**

```python
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
    health_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    lines = []
    for r in snapshot.get("repos", []):
        icon = health_icon.get(r.get("health", ""), "⚪")
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
        message = f"☀️ Wingmen Morning Brief — {date_str}\n\n{status}\n\n💡 CTO Recommendations:\n{analysis}"

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
```

- [ ] **Step 2: Commit**

```bash
git add nervous_system/morning_brief.py
git commit -m "feat: add morning_brief — 6AM strategic Telegram brief with CTO recommendations"
```

---

### Task 4: weekly_digest — Sunday strategic review

**Files:**
- Create: `nervous_system/weekly_digest.py`

- [ ] **Step 1: Create `nervous_system/weekly_digest.py`**

```python
"""weekly_digest — Sunday 9PM strategic weekly review."""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger("wingmen.nervous_system.weekly_digest")

CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
CTO_PRINCIPLES_PATH = os.path.join(os.path.dirname(__file__), "..", "CTO_PRINCIPLES.md")
CLAUDE_ENV = {
    "HOME": os.path.expanduser("~"),
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "USER": os.environ.get("USER", ""),
    "SHELL": os.environ.get("SHELL", ""),
    "LANG": os.environ.get("LANG", ""),
}


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

        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "-p", prompt, "--output-format", "text",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=CLAUDE_ENV,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
            digest = stdout.decode(errors="replace").strip()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            digest = "(Weekly analysis timed out)"

        now = datetime.now(timezone.utc)
        message = f"📊 Wingmen Weekly Digest — Week of {now.strftime('%d %b %Y')}\n\n{digest}"

        await bot.send_message(chat_id=admin_chat_id, text=message[:4000])

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
```

- [ ] **Step 2: Commit**

```bash
git add nervous_system/weekly_digest.py
git commit -m "feat: add weekly_digest — Sunday 9PM strategic review with barakah check"
```

---

### Task 5: memory_sync — midnight snapshot differ

**Files:**
- Create: `nervous_system/memory_sync.py`

- [ ] **Step 1: Create `nervous_system/memory_sync.py`**

```python
"""memory_sync — midnight diff of latest two snapshots, flag significant changes."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("wingmen.nervous_system.memory_sync")


async def memory_sync(supabase):
    """Diff latest two snapshots and log significant changes."""
    start = time.monotonic()
    logger.info("memory_sync starting...")

    try:
        result = await supabase.table("wingmen_brain").select("*").order("snapshot_at", desc=True).limit(2).execute()
        snapshots = result.data or []

        if len(snapshots) < 2:
            logger.info("memory_sync: fewer than 2 snapshots — nothing to diff")
            return

        current = snapshots[0]
        previous = snapshots[1]

        changes = []

        # Compare repos
        current_repos = {r["name"]: r for r in current.get("repos", [])}
        previous_repos = {r["name"]: r for r in previous.get("repos", [])}

        # New repos
        for name in current_repos:
            if name not in previous_repos:
                changes.append(f"New repo detected: {name}")

        # Removed repos
        for name in previous_repos:
            if name not in current_repos:
                changes.append(f"Repo removed: {name}")

        # Health changes
        for name, curr in current_repos.items():
            prev = previous_repos.get(name, {})
            if curr.get("health") != prev.get("health"):
                changes.append(f"{name}: health {prev.get('health', '?')} → {curr.get('health', '?')}")
            if curr.get("blockers") and not prev.get("blockers"):
                changes.append(f"{name}: new blockers — {', '.join(curr['blockers'][:3])}")

        # Client changes
        current_clients = {c["name"] for c in current.get("clients", [])}
        previous_clients = {c["name"] for c in previous.get("clients", [])}
        for name in current_clients - previous_clients:
            changes.append(f"New client: {name}")
        for name in previous_clients - current_clients:
            changes.append(f"Client removed: {name}")

        # Sync health change
        if current.get("sync_health") != previous.get("sync_health"):
            changes.append(f"Sync health: {previous.get('sync_health', '?')} → {current.get('sync_health', '?')}")

        # Log results
        duration = int((time.monotonic() - start) * 1000)
        details = "\n".join(changes) if changes else "No significant changes"

        await supabase.table("brain_sync_log").insert({
            "task_name": "memory_sync",
            "status": "success",
            "duration_ms": duration,
            "details": details,
        }).execute()

        if changes:
            logger.info(f"memory_sync: {len(changes)} changes detected")
            for c in changes:
                logger.info(f"  → {c}")
        else:
            logger.info("memory_sync: no significant changes")

    except Exception as e:
        logger.error(f"memory_sync failed: {e}")
        try:
            await supabase.table("brain_sync_log").insert({
                "task_name": "memory_sync", "status": "failed", "details": str(e)[:500],
            }).execute()
        except Exception:
            pass
```

- [ ] **Step 2: Commit**

```bash
git add nervous_system/memory_sync.py
git commit -m "feat: add memory_sync — midnight snapshot differ with change detection"
```

---

### Task 6: session_compress — 2 AM checkpoint writer

**Files:**
- Create: `nervous_system/session_compress.py`

- [ ] **Step 1: Create `nervous_system/session_compress.py`**

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add nervous_system/session_compress.py
git commit -m "feat: add session_compress — 2AM checkpoint writer for context recovery"
```

---

### Task 7: Wire scheduler into cto_bot.py

**Files:**
- Modify: `cto_bot.py` (main function)

- [ ] **Step 1: Add imports and scheduler setup in `main()`**

At the top of cto_bot.py, add import:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
```

In the `main()` function, after `app.add_error_handler(error_handler)` and before `app.run_polling(...)`, add:

```python
    # ── Nervous System: scheduled tasks ──
    async def post_init_scheduler(application):
        await _recover_unprocessed(application)
        try:
            await asyncio.to_thread(_get_whisper)
            logger.info("Whisper model pre-loaded")
        except Exception as e:
            logger.warning(f"Failed to pre-load Whisper model: {e}")

        # Start nervous system scheduler
        from nervous_system.brain_sync import brain_sync
        from nervous_system.morning_brief import morning_brief
        from nervous_system.weekly_digest import weekly_digest
        from nervous_system.memory_sync import memory_sync
        from nervous_system.session_compress import session_compress

        supabase_client = await get_supabase()
        admin_chat_id = os.environ.get("MUSA_TELEGRAM_ID", "286619815")

        scheduler = AsyncIOScheduler(timezone="Asia/Singapore")

        scheduler.add_job(
            brain_sync, "interval", hours=4, args=[supabase_client],
            id="brain_sync", replace_existing=True,
        )
        scheduler.add_job(
            morning_brief, "cron", hour=6, args=[supabase_client, application.bot, admin_chat_id],
            id="morning_brief", replace_existing=True,
        )
        scheduler.add_job(
            weekly_digest, "cron", day_of_week="sun", hour=21, args=[supabase_client, application.bot, admin_chat_id],
            id="weekly_digest", replace_existing=True,
        )
        scheduler.add_job(
            memory_sync, "cron", hour=0, args=[supabase_client],
            id="memory_sync", replace_existing=True,
        )
        scheduler.add_job(
            session_compress, "cron", hour=2, args=[supabase_client],
            id="session_compress", replace_existing=True,
        )

        scheduler.start()
        logger.info("Nervous system scheduler started — 5 tasks registered")

        # Run brain_sync immediately on startup
        asyncio.create_task(brain_sync(supabase_client))
```

Replace the existing `app.post_init = post_init` with `app.post_init = post_init_scheduler`.

- [ ] **Step 2: Verify syntax**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python3 -m py_compile cto_bot.py`
Expected: No output (clean)

- [ ] **Step 3: Commit**

```bash
git add cto_bot.py
git commit -m "feat: wire nervous system scheduler into cto_bot — 5 scheduled tasks"
```

---

### Task 8: CTO Principles + Brainstorm integration

**Files:**
- Create: `CTO_PRINCIPLES.md`
- Modify: `agents/brainstorm.py`

- [ ] **Step 1: Create `CTO_PRINCIPLES.md`**

```markdown
# CTO Principles — Wingmen

## Business Priorities (in order)
1. Revenue-generating work — it funds everything else
2. Client satisfaction — deliver what was promised
3. Infrastructure — make the system more reliable and efficient
4. Open-source / community — waqf for the ummah

## Islamic Constraints (non-negotiable)
- Halal revenue only — no riba (interest), no gambling mechanics, no deceptive practices
- Zakat transparency — any system handling zakat must be fully auditable
- Amanah (trust) — client data is a trust, not an asset. Minimize collection, maximize protection.
- No vendor lock-in that traps clients — they should be able to leave with their data
- Waqf mindset — open-source contributions are sadaqah jariyah (ongoing charity)

## Decision Framework
- When in doubt, choose the simpler solution
- When two options have equal merit, choose the one that helps more people
- When choosing between speed and correctness, choose correctness for anything touching money or knowledge
- When choosing between profit and community impact, find the path that serves both

## What the CTO Should Flag
- Clients going quiet (>7 days no interaction) — proactive check-in needed
- Revenue concentration risk (>50% from one client)
- Technical debt aging (>2 weeks unaddressed)
- Open-source repos without recent contribution (waqf neglect)
- Any architecture decision that creates dependency on a single provider
```

- [ ] **Step 2: Modify `agents/brainstorm.py` to load CTO principles for admin**

At the top of the file, add:

```python
import os
from pathlib import Path

CTO_PRINCIPLES_PATH = Path(__file__).parent.parent / "CTO_PRINCIPLES.md"
```

In the `_admin_persona` function, before the closing `"""`, add:

```python
    # Load CTO principles if available
    principles = ""
    if CTO_PRINCIPLES_PATH.exists():
        principles = f"\n## CTO PRINCIPLES\n{CTO_PRINCIPLES_PATH.read_text()}\n"
```

And include `{principles}` in the returned string, after the COMMANDS section.

- [ ] **Step 3: Commit**

```bash
git add CTO_PRINCIPLES.md agents/brainstorm.py
git commit -m "feat: add CTO_PRINCIPLES.md and load into admin brainstorm persona"
```

---

### Task 9: Test and restart

- [ ] **Step 1: Install APScheduler if missing**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/pip install apscheduler
```

- [ ] **Step 2: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 3: Restart bot**

```bash
launchctl unload ~/Library/LaunchAgents/dev.wingmen.ctobot.plist && sleep 2 && launchctl load ~/Library/LaunchAgents/dev.wingmen.ctobot.plist
```

- [ ] **Step 4: Verify scheduler started**

```bash
sleep 10 && grep "Nervous system scheduler\|brain_sync" ~/wingmen/orchestrator/logs/cto_bot.log | tail -5
```

Expected: "Nervous system scheduler started — 5 tasks registered" and "brain_sync starting..."

- [ ] **Step 5: Verify BRAIN.md generated**

```bash
cat ~/wingmen/BRAIN.md
```

Expected: Repo status summary with health indicators.

- [ ] **Step 6: Verify snapshot in Supabase**

Query via Supabase MCP:
```sql
SELECT id, snapshot_at, sync_health FROM wingmen_brain ORDER BY snapshot_at DESC LIMIT 1;
```

Expected: 1 row with sync_health = "healthy" or "degraded".
