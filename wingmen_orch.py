"""Wingmen Orchestrator — main async worker loop.

Polls Supabase jobs table every 30s, picks highest priority queued job,
runs it through the build pipeline, and reports results.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from supabase import acreate_client

from aiohttp import web

import context_loader
import spec_generator
import ralph_runner
import deploy_manager
import status_reporter
import test_gate
import build_audit
import semantic_drift
from nervous_system.bug_escalation import check_stale_bugs
from nervous_system.paused_job_escalation import check_paused_jobs
from nervous_system.queue_stall_detector import check_queue_stalls
from bug_pipeline import poll_undiagnosed_bugs
from nervous_system.conversation_cleanup import cleanup_expired_conversations
from nervous_system.council_summary import summarize_pending_sessions
from nervous_system.council_relay import relay_council_messages
from nervous_system.council_agent import run_council_agent
from nervous_system.feature_health_signal import collect_feature_health
from nervous_system.council_executor import poll_executor
from nervous_system.strategic_decisions_poll import poll_strategic_decisions, notify_decision_complete
from nervous_system.cai_review_request import poll_cai_review_requests
from nervous_system.agent_messages_poll import poll_agent_messages
from nervous_system.pipeline_clock import tick_pipeline_clock
from nervous_system.agent_watchdog import check_agent_health
from nervous_system.qa_bridge import poll_qa_findings
from uptime_monitor import poll_uptime
from nervous_system.schema_gate import check_and_block as schema_gate_check
from nervous_system.archive import run_archive
from nervous_system.wingmen_dream import run_dream
from nervous_system.ecosystem_auditor import (
    run_frequent_gates,
    run_half_hour_gates,
    run_hourly_gates,
    run_six_hour_gates,
    run_daily_gates,
)
from heartbeat import write_orchestrator_heartbeat
from nervous_system.swallowed_except_harness import record_swallowed

# ── Setup ────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("wingmen.orch")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
MAX_FAIL_COUNT = int(os.environ.get("MAX_FAIL_COUNT", "3"))
STALE_JOB_MINUTES = int(os.environ.get("STALE_JOB_MINUTES", "120"))
MAX_CONCURRENT_BUILDS = int(os.environ.get("MAX_CONCURRENT_BUILDS", "3"))

# ARCH-030: tracks job IDs with an in-flight escalation session so a single
# paused job cannot spawn multiple simultaneous CC processes.
_arch030_active: set[int] = set()


def _autocc_poll_enabled() -> bool:
    """ARCH-016 / MUSA-001 / CAI-RESP-022: gate the autocc job-picking + auto-queue
    paths so the orchestrator can run for heartbeat / monitoring / bug-pipeline
    purposes without claiming new strategic_decisions jobs.

    Read at every poll iteration so flipping the env doesn't require restart.
    Default true to preserve legacy behaviour.
    """
    return os.environ.get("AUTOCC_POLL_ENABLED", "true").lower() not in (
        "false", "0", "no", "off",
    )


def _arch030_escalation_enabled() -> bool:
    """ARCH-030: gate the auto-escalation CC spawn.
    Default true. Set ARCH030_ESCALATION_ENABLED=false to disable without restart.
    """
    return os.environ.get("ARCH030_ESCALATION_ENABLED", "true").lower() not in (
        "false", "0", "no", "off",
    )


_supabase = None
_supabase_lock = asyncio.Lock()


async def get_supabase():
    global _supabase
    if _supabase is not None:
        return _supabase
    async with _supabase_lock:
        if _supabase is None:
            url = os.environ["SUPABASE_URL"]
            key = os.environ["SUPABASE_SERVICE_KEY"]
            _supabase = await acreate_client(url, key)
        return _supabase


async def pick_next_jobs(supabase, running_repos: set[str], max_picks: int) -> list[dict]:
    """Pick up to max_picks jobs, one per repo, skipping repos with running jobs.

    Uses CAS pattern to prevent race conditions.
    """
    if max_picks <= 0:
        return []

    result = await (
        supabase.table("jobs")
        .select("*")
        .eq("status", "queued")
        .order("priority", desc=False)
        .order("created_at", desc=False)
        .limit(20)  # fetch enough candidates
        .execute()
    )
    if not result.data:
        return []

    picked = []
    claimed_repos = set()

    for job in result.data:
        if len(picked) >= max_picks:
            break

        repo = job["repo_name"]
        # Skip if this repo already has a running job (from us or previous poll)
        if repo in running_repos or repo in claimed_repos:
            continue

        # Atomic claim
        claim = await (
            supabase.table("jobs")
            .update({"status": "running", "updated_at": "now()"})
            .eq("id", job["id"])
            .eq("status", "queued")
            .execute()
        )
        if claim.data:
            picked.append(claim.data[0])
            claimed_repos.add(repo)
            logger.info(f"Claimed job #{job['id']} for {repo}")

    return picked


async def recover_stale_jobs(supabase) -> None:
    """Requeue jobs stuck in 'running' state for too long."""
    try:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_JOB_MINUTES)).isoformat()
        result = await (
            supabase.table("jobs")
            .select("id, repo_name")
            .eq("status", "running")
            .lt("updated_at", cutoff)
            .execute()
        )
        for job in (result.data or []):
            await (
                supabase.table("jobs")
                .update({"status": "queued", "result_summary": f"Auto-requeued: stuck running for >{STALE_JOB_MINUTES}min"})
                .eq("id", job["id"])
                .execute()
            )
            logger.warning(f"Recovered stale job #{job['id']} ({job['repo_name']})")
    except Exception as e:
        logger.error(f"Stale job recovery failed: {e}")


async def cleanup_zombie_jobs(supabase) -> int:
    """Mark all 'running' jobs as failed on startup — they were mid-execution when the process died.

    Spec conformance (TASK-033 + CC-UPDATE-015 deviation fix #3):
    - Marks status='failed' (not 'queued') — safer: prevents infinite restart
      loops on crash-causing jobs. Manual /resume required.
    - Matches ALL 'running' rows — startup semantics; no age filter needed.
    - Bumps fail_count so a repeatedly-zombied job eventually hits the
      3-fail pause threshold instead of flapping forever.
    """
    try:
        result = await (
            supabase.table("jobs")
            .select("id, repo_name, fail_count")
            .eq("status", "running")
            .execute()
        )
        zombies = result.data or []
        for job in zombies:
            new_fail_count = (job.get("fail_count") or 0) + 1
            await (
                supabase.table("jobs")
                .update({
                    "status": "failed",
                    "result_summary": "Zombie: was running when orchestrator restarted",
                    "fail_count": new_fail_count,
                })
                .eq("id", job["id"])
                .execute()
            )
            logger.warning(
                f"Zombie cleanup: job #{job['id']} ({job['repo_name']}) marked failed, "
                f"fail_count={new_fail_count}"
            )
        return len(zombies)
    except Exception as e:
        logger.error(f"Zombie job cleanup failed: {e}")
        return 0


async def set_job_status(supabase, job_id: int, status: str, **extra):
    update = {"status": status, "updated_at": "now()"}
    update.update(extra)
    await supabase.table("jobs").update(update).eq("id", job_id).execute()


async def _ensure_repo(repo_path: str, github_url: str) -> None:
    """Clone the repo if it doesn't exist locally."""
    if Path(repo_path).exists():
        return

    logger.info(f"  Repo not found at {repo_path}, cloning from {github_url}")
    parent = str(Path(repo_path).parent)
    Path(parent).mkdir(parents=True, exist_ok=True)
    dir_name = Path(repo_path).name

    proc = await asyncio.create_subprocess_exec(
        "gh", "repo", "clone", github_url, dir_name,
        cwd=parent,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0:
            logger.info(f"  Cloned {github_url} → {repo_path}")
        else:
            raise RuntimeError(f"Clone failed: {stderr.decode(errors='replace')}")
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Clone timed out for {github_url}")


async def _git_pull(repo_path: str) -> None:
    """Pull latest changes before running a build."""
    git_dir = Path(repo_path) / ".git"
    if not git_dir.exists():
        logger.info(f"  No .git in {repo_path}, skipping pull")
        return

    proc = await asyncio.create_subprocess_exec(
        "git", "pull", "--ff-only",
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0:
            logger.info(f"  Git pull OK: {stdout.decode(errors='replace').strip()}")
        else:
            logger.warning(f"  Git pull failed: {stderr.decode(errors='replace').strip()}")
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("  Git pull timed out")


async def _check_clean_tree(repo_path: str) -> tuple:
    """Check that the working tree has no uncommitted or untracked files."""
    proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        return False, "git status timed out"

    output = stdout.decode(errors="replace").strip()
    if not output:
        return True, ""
    return False, output


async def _git_push(repo_path: str, job_id: int, description: str) -> None:
    """Stage, commit, and push changes made by Claude CLI."""
    async def _run(cmd, timeout=60):
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "", "timeout"

    rc, out, _ = await _run(["git", "status", "--porcelain"])
    if not out.strip():
        logger.info(f"  No changes to commit for job #{job_id}")
        return

    await _run(["git", "add", "-A"])

    # Sanitize description for commit message
    safe_desc = description[:100].replace('"', "'").replace('\n', ' ')
    msg = f"feat: {safe_desc} [job_{job_id}]"
    rc, out, err = await _run(["git", "commit", "-m", msg])
    if rc != 0:
        logger.warning(f"  Git commit failed: {err}")
        return

    rc, out, err = await _run(["git", "push"], timeout=120)
    if rc != 0:
        logger.warning(f"  Git push failed: {err}")
        return

    logger.info(f"  Committed and pushed: {msg}")


async def _capture_git_info(repo_path: str) -> dict:
    """Capture commit SHA, files changed, and diff stat from the last commit."""
    info = {"commit_sha": None, "files_changed": [], "diff_summary": None}

    async def _run(cmd):
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            return stdout.decode(errors="replace").strip() if proc.returncode == 0 else None
        except asyncio.TimeoutError:
            proc.kill()
            return None

    info["commit_sha"] = await _run(["git", "rev-parse", "HEAD"])

    names = await _run(["git", "diff", "--name-only", "HEAD~1"])
    if names:
        info["files_changed"] = [f for f in names.splitlines() if f.strip()]

    info["diff_summary"] = await _run(["git", "diff", "--stat", "HEAD~1"])

    return info


async def _write_work_output(supabase, job_id, repo_name, **fields):
    """Write structured work output to Supabase for CAI visibility."""
    row = {"job_id": job_id, "repo_name": repo_name}
    row.update(fields)
    await supabase.table("work_outputs").insert(row).execute()
    logger.info(f"  Wrote work_output for job #{job_id}")


async def _write_work_session(supabase, job_id, repo_name, **fields):
    """Write a narrative work-session record to Supabase (BUG-006)."""
    row = {"job_id": job_id, "repo_name": repo_name}
    row.update(fields)
    await supabase.table("cc_work_sessions").insert(row).execute()
    logger.info(f"  Wrote cc_work_session for job #{job_id}")


def _build_narrative(job, outcome, result_summary="", deploy_url=None, elapsed=None):
    """Build a human-readable narrative string for a work session."""
    desc = job.get("description", "unknown task")
    triggered = job.get("triggered_by", "unknown")
    parts = [f"Job #{job['id']} ({desc}), triggered by {triggered}."]
    if outcome == "success":
        parts.append(f"Completed successfully in {elapsed:.0f}s." if elapsed else "Completed successfully.")
        if deploy_url:
            parts.append(f"Deployed to {deploy_url}.")
    elif outcome == "failed":
        parts.append(f"Failed after {elapsed:.0f}s." if elapsed else "Failed.")
    elif outcome == "crashed":
        parts.append(f"Crashed after {elapsed:.0f}s." if elapsed else "Crashed.")
    if result_summary:
        parts.append(f"Summary: {result_summary[:500]}")
    return " ".join(parts)


async def _resolve_client_chat_id(supabase, job: dict) -> str | None:
    """Look up the Telegram chat_id for the client who triggered this job."""
    if not job.get("client_id"):
        return None
    try:
        result = await supabase.table("clients").select("telegram_chat_id").eq(
            "id", job["client_id"]
        ).limit(1).execute()
        if result.data:
            return result.data[0].get("telegram_chat_id")
    except Exception as e:
        logger.warning(f"Failed to resolve client chat_id: {e}")
        record_swallowed("client_chat_lookup", e)
    return None


async def _spawn_escalation_session(
    supabase,
    job: dict,
    result_summary: str,
    session_prompt: str,
    repo_path: str,
) -> None:
    """ARCH-030: spawn a dangerous-mode CC session to self-diagnose a paused job.

    Fire-and-forget from run_job's failure handler. Collects git state and
    build_log context, builds a rich prompt, runs `claude --dangerously-skip-permissions -p`,
    then posts the diagnosis back to agent_messages. Does NOT re-enter the
    ralph_runner pipeline — CC acts directly on the repo and jobs table.
    """
    global _arch030_active
    job_id = job["id"]
    repo_name = job.get("repo_name", "unknown")

    if job_id in _arch030_active:
        logger.info(f"ARCH-030: escalation already in-flight for job #{job_id}, skipping duplicate")
        return
    _arch030_active.add(job_id)

    try:
        # ── Gather diagnostics ──────────────────────────────────────────────
        async def _git(cmd: list[str]) -> str:
            if not repo_path or not Path(repo_path).exists():
                return ""
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=repo_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                return stdout.decode(errors="replace").strip()
            except Exception:
                return ""

        git_status = await _git(["git", "status", "--short"])
        git_log = await _git(["git", "log", "--oneline", "-10"])

        build_log_tail = ""
        try:
            bl = await (
                supabase.table("build_log")
                .select("phase,message,level")
                .eq("job_id", job_id)
                .order("created_at", desc=True)
                .limit(10)
                .execute()
            )
            if bl.data:
                lines = [
                    f"[{r['level']}] {r['phase']}: {r['message'][:200]}"
                    for r in reversed(bl.data)
                ]
                build_log_tail = "\n".join(lines)
        except Exception:
            pass

        fail_count_display = f"{job.get('fail_count', 0) + 1}/{MAX_FAIL_COUNT}"

        escalation_prompt = f"""ARCH-030 AUTO-ESCALATION: Job #{job_id} paused after {fail_count_display} failures.

You are an emergency diagnostic session for the Wingmen orchestrator. Act immediately — do not acknowledge this preamble.

## Job
- ID: {job_id}
- Repo: {repo_name}
- Description: {job.get('description', 'unknown')}
- Fail count: {fail_count_display}

## Original Session Prompt (what was attempted)
{session_prompt[:3000]}

## Failure Summary
{result_summary[:1000]}

## Build Log (recent)
{build_log_tail[:800] or '(none)'}

## Git State ({repo_path})
Status:
{git_status[:500] or '(clean)'}

Recent commits:
{git_log[:500] or '(none)'}

## Instructions
1. Read the relevant source files. Understand WHY the job failed.
2. If the root cause is fixable within ~20 minutes:
   a. Fix it (edit files, run tests).
   b. Commit: `fix(arch030): auto-diagnosis job #{job_id} — <cause>`
   c. Update the jobs row: SET status='queued', fail_count=0, result_summary='ARCH-030: fixed — <what you fixed>' WHERE id={job_id}. Use the Supabase service key from .env.
3. If NOT fixable autonomously (schema migration required, ambiguous requirements, needs Musa):
   a. Write a concrete, explicit rewrite of the spec and update: SET session_prompt=<new prompt>, description=<clearer title>, status='queued', fail_count=0 WHERE id={job_id}.
   b. If you need Musa's input: leave status='paused', set requires_response=True below.
4. Post your diagnosis to Supabase agent_messages:
   INSERT (from_agent='arch-030-escalation', to_agent='cc-ihsanos', message_type='update',
           subject='ARCH-030 Job #{job_id}: <outcome in 60 chars>',
           body=<your diagnosis + what you did, under 2000 chars>,
           requires_response=<True only if human input genuinely required>)
5. Do not create new jobs. Do not modify other jobs. Scope = job #{job_id} in {repo_path}.
"""

        logger.info(f"ARCH-030: spawning escalation CC for job #{job_id} ({len(escalation_prompt)} chars)")

        # Announce escalation start
        try:
            await supabase.table("agent_messages").insert({
                "from_agent": "arch-030-escalation",
                "to_agent": "cc-ihsanos",
                "message_type": "update",
                "subject": f"ARCH-030: starting auto-diagnosis for job #{job_id}",
                "body": (
                    f"Job #{job_id} ({job.get('description','')[:80]}) paused after "
                    f"{fail_count_display} failures. Launching dangerous-mode CC.\n\n"
                    f"Failure: {result_summary[:400]}"
                ),
                "requires_response": False,
            }).execute()
        except Exception as _e:
            record_swallowed("arch030_start_msg", _e)

        # ── Spawn dangerous-mode CC ─────────────────────────────────────────
        proc = await asyncio.create_subprocess_exec(
            "claude", "--dangerously-skip-permissions", "-p", escalation_prompt,
            cwd=repo_path or str(Path(__file__).parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "AUTOCC_POLL_ENABLED": "false"},  # prevent nested orchestrator re-entry
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)  # 15 min cap
            cc_output = stdout.decode(errors="replace").strip()
            cc_err = stderr.decode(errors="replace").strip()
            rc = proc.returncode

            logger.info(f"ARCH-030: escalation session done for job #{job_id}, rc={rc}")

            body_parts = [f"Escalation session exit code: {rc}"]
            if cc_output:
                body_parts.append(f"\nCC output (tail):\n{cc_output[-2000:]}")
            if cc_err and rc != 0:
                body_parts.append(f"\nStderr:\n{cc_err[-400:]}")

            await supabase.table("agent_messages").insert({
                "from_agent": "arch-030-escalation",
                "to_agent": "cc-ihsanos",
                "message_type": "update",
                "subject": f"ARCH-030 Job #{job_id}: escalation {'done' if rc == 0 else 'errored (rc=' + str(rc) + ')'}",
                "body": "\n".join(body_parts)[:5000],
                "requires_response": rc != 0,
            }).execute()

        except asyncio.TimeoutError:
            proc.kill()
            logger.warning(f"ARCH-030: escalation timed out for job #{job_id} (15 min)")
            try:
                await supabase.table("agent_messages").insert({
                    "from_agent": "arch-030-escalation",
                    "to_agent": "cc-ihsanos",
                    "message_type": "blocker",
                    "subject": f"ARCH-030 Job #{job_id}: escalation timed out (15 min cap)",
                    "body": (
                        f"Auto-escalation session for job #{job_id} ({job.get('description','')[:80]}) "
                        f"exceeded the 15-minute cap and was killed. Manual review required."
                    ),
                    "requires_response": True,
                }).execute()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"ARCH-030: escalation session failed for job #{job_id}: {e}")
        record_swallowed("arch030_escalation", e)

    finally:
        _arch030_active.discard(job_id)


async def run_job(supabase, job: dict) -> None:
    """Execute the full build pipeline for a single job."""
    job_id = job["id"]
    repo_name = job["repo_name"]
    start_time = time.monotonic()

    logger.info(f"▶ Starting job #{job_id}: {repo_name} — {job['description']}")

    client_chat_id = await _resolve_client_chat_id(supabase, job)

    async def notify(jid, rname, phase, detail=""):
        await status_reporter.notify_progress(jid, rname, phase, detail, client_chat_id, supabase)

    try:
        # 1. Load context
        await notify(job_id, repo_name, "picked", job["description"])
        context = await context_loader.load_context(repo_name, supabase)
        logger.info(f"  Context loaded for {repo_name}")
        await notify(job_id, repo_name, "context", "Loaded project context")

        # 2. Ensure repo exists locally, then pull + tag
        await _ensure_repo(context["repo_path"], context["repo_config"].get("github", ""))
        await _git_pull(context["repo_path"])
        try:
            tag = f"pre-job-{job_id}"
            proc = await asyncio.create_subprocess_exec(
                "git", "tag", "-f", tag,
                cwd=context["repo_path"],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception as e:
            record_swallowed("git_tag_cleanup", e)

        # 2b. Pre-flight: stash dirty working tree (BUG-019 replaces abort).
        # CC now runs in an isolated git worktree, so pre-existing dirt in the
        # main tree can't contaminate the build. Auto-stash clears the path
        # and preserves the files for later inspection via `git stash list`.
        clean, dirty_files = await _check_clean_tree(context["repo_path"])
        if not clean:
            warn_msg = (
                f"BUG-019: main tree is dirty before job #{job_id} — "
                f"auto-stashing. Files:\n{dirty_files[:300]}"
            )
            logger.warning(f"  {warn_msg}")
            await notify(job_id, repo_name, "warn", warn_msg[:200])
            stash_proc = await asyncio.create_subprocess_exec(
                "git", "stash", "push", "-u",
                "-m", f"BUG-019 auto-stash before job #{job_id}",
                cwd=context["repo_path"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stash_err = await asyncio.wait_for(stash_proc.communicate(), timeout=30)
                if stash_proc.returncode != 0:
                    logger.warning(
                        f"  BUG-019: git stash failed: {stash_err.decode().strip()}"
                    )
            except Exception as stash_exc:
                logger.warning(f"  BUG-019: git stash error: {stash_exc}")

        # 3. Generate spec prompt
        prompt_text = await spec_generator.generate_spec(job, context)
        logger.info(f"  Spec generated ({len(prompt_text)} chars)")

        # 3b. Validate spec
        valid, errors = spec_generator.validate_spec(prompt_text, job_id)
        if not valid:
            error_msg = f"Spec validation failed: {'; '.join(errors)}"
            logger.warning(f"  {error_msg}")
            # Persist what the CLI actually returned so spec failures stop being
            # blind. Job 35 hit this on 2026-04-14 with zero visibility into
            # what came back from Claude CLI.
            try:
                await supabase.table("build_log").insert({
                    "job_id": job_id,
                    "repo_name": repo_name,
                    "phase": "spec_invalid",
                    "message": f"VALIDATION FAILED: {'; '.join(errors)}\n--- CLI OUTPUT (first 2000 chars) ---\n{(prompt_text or '')[:2000]}",
                    "level": "error",
                }).execute()
            except Exception as log_e:
                logger.warning(f"Could not persist invalid-spec diagnostic: {log_e}")
                record_swallowed("spec_diagnostic_persist", log_e)
            await notify(job_id, repo_name, "failed", error_msg)
            raise RuntimeError(error_msg)
        await notify(job_id, repo_name, "spec", f"Spec validated OK ({len(prompt_text)} chars)")

        # 4. Run Claude CLI
        await notify(job_id, repo_name, "claude", "Building...")
        job_started_at = datetime.now(timezone.utc)  # ARCH-021 Gate 1 anchor
        result = await ralph_runner.run_claude(
            repo_path=context["repo_path"],
            prompt_text=prompt_text,
            job_id=job_id,
            repo_name=repo_name,
            supabase=supabase,
            job_started_at=job_started_at,
            decision_text=prompt_text,
            commit_expected=job.get("commit_expected", True),
        )
        logger.info(f"  Claude done: success={result['success']}")

        # 4b. Post-build test gate
        if result["success"]:
            test_result = await test_gate.run_tests(
                context["repo_path"], repo_name, job_id, supabase
            )
            if not test_result["passed"]:
                logger.warning(f"  Test gate failed: {test_result['output'][:200]}")
                await notify(job_id, repo_name, "failed", f"Tests failed: {test_result['output'][:200]}")
                result["success"] = False
                result["summary"] = f"Tests failed: {test_result['output'][:500]}"

        elapsed = time.monotonic() - start_time

        if result["success"]:
            # 5. Git commit + push
            try:
                await notify(job_id, repo_name, "deploy", "Pushing changes...")
                await _git_push(context["repo_path"], job_id, job["description"])
            except Exception as e:
                logger.warning(f"  Git push failed: {e}")
                record_swallowed("git_push", e)

            # 5c. Capture git info for work_outputs
            git_info = await _capture_git_info(context["repo_path"])

            # 5d. Schema gate (ARCH-015) — if the commit touched schema.sql,
            # pause the job until Musa applies the migration. The orchestrator
            # has no DB DDL credentials by design.
            try:
                from telegram import Bot as _SGBot
                _sg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                _sg_bot = _SGBot(token=_sg_token) if _sg_token else None
                blocked = await schema_gate_check(
                    supabase,
                    context["repo_path"],
                    repo_name,
                    job_id,
                    job.get("description", ""),
                    _sg_bot,
                )
                if blocked:
                    await notify(job_id, repo_name, "paused",
                                 "Schema migration pending — see Telegram for SQL.")
                    logger.warning(f"\u23f8 Job #{job_id} blocked on schema migration")
                    return
            except Exception as e:
                # Gate is belt-and-braces — don't let a bug in it wedge the pipeline.
                logger.error(f"Schema gate error (non-blocking): {e}")
                record_swallowed("schema_gate", e)

            # 5b. Random audit (advisory, non-blocking)
            try:
                await build_audit.maybe_audit(context["repo_path"], job_id, repo_name, supabase)
            except Exception as e:
                logger.warning(f"  Random audit error (non-blocking): {e}")
                record_swallowed("random_audit", e)

            # 6. Deploy
            deploy_result = {"deployed": False, "url": None}
            try:
                deploy_result = await deploy_manager.deploy(repo_name)
                if deploy_result["deployed"]:
                    await notify(job_id, repo_name, "deploy", f"Live at {deploy_result['url']}")
                    logger.info(f"  Deployed → {deploy_result['url']}")
            except Exception as e:
                logger.error(f"  Deploy failed: {e}")
                record_swallowed("deploy_failure", e)

            # 7. Write work output to Supabase (ARCH-010) — must succeed before marking completed
            await _write_work_output(
                supabase, job_id, repo_name,
                build_spec=prompt_text[:50000],
                commit_sha=git_info.get("commit_sha"),
                files_changed=git_info.get("files_changed", []),
                diff_summary=(git_info.get("diff_summary") or "")[:10000],
                deploy_url=deploy_result.get("url"),
                cc_output_summary=result["summary"][:5000],
                test_passed=True,
                success=True,
                gate1_result=result.get("gate1"),
                gate2_result=result.get("gate2"),
            )
            await build_audit.verify_work_output(supabase, job_id, repo_name)
            await _write_work_session(
                supabase, job_id, repo_name,
                triggered_by=job.get("triggered_by"),
                session_prompt=prompt_text[:50000],
                narrative=_build_narrative(job, "success", result["summary"], deploy_result.get("url"), elapsed),
                outcome="success",
                duration_seconds=int(elapsed),
                files_changed=git_info.get("files_changed", []),
                commit_sha=git_info.get("commit_sha"),
                deploy_url=deploy_result.get("url"),
            )

            # 8. Report success
            await status_reporter.report(
                job=job,
                result=result,
                deploy_url=deploy_result.get("url"),
                elapsed_seconds=elapsed,
                client_chat_id=client_chat_id,
                supabase=supabase,
            )
            await set_job_status(
                supabase, job_id, "completed",
                result_summary=result["summary"][:2000],
            )
            logger.info(f"✅ Job #{job_id} completed in {elapsed:.0f}s")

            # BUG-017: push completion event to cc-ihsanos inbox (ARCH-018/ARCH-028)
            try:
                _g1 = (result.get("gate1") or {})
                _g2 = (result.get("gate2") or {})
                _sha = _g1.get("commit_sha", "")
                _body = (
                    f"{result['summary'][:800]}\n\n"
                    f"Gate1 (commit): {'pass' if _g1.get('passed') else 'n/a'}{' sha=' + _sha if _sha else ''}\n"
                    f"Gate2 (intent): {'pass conf=' + str(_g2.get('confidence')) if _g2.get('passed') else 'n/a'}"
                )
                await supabase.table("agent_messages").insert({
                    "from_agent": "ralph_runner",
                    "to_agent": "cc-ihsanos",
                    "message_type": "update",
                    "subject": f"Job #{job_id} completed: {job.get('description', '')[:80]}",
                    "body": _body,
                    "requires_response": False,
                }).execute()
            except Exception as _e:
                record_swallowed("bug017_completion_msg", _e)

            # Notify Musa if this was a strategic decision auto-implementation
            if job.get("triggered_by") == "strategic_decisions_poll":
                import re as _re
                _match = _re.match(r"\[([A-Z]+-\d+)\]", job.get("description", ""))
                if _match:
                    try:
                        from telegram import Bot as _Bot
                        _bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                        _musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                        if _bot_token and _musa_id:
                            _bot = _Bot(token=_bot_token)
                            await notify_decision_complete(
                                supabase, _match.group(1), job_id, True,
                                result["summary"][:500],
                                _bot, _musa_id,
                            )
                    except Exception as _e:
                        logger.error(f"Strategic decision completion notify failed: {_e}")
                        record_swallowed("decision_notify_success", _e)

            # Log usage
            try:
                await supabase.table("usage_log").insert({
                    "client_id": job.get("client_id"),
                    "action_type": "build_completed",
                    "repo_name": repo_name,
                    "duration_seconds": elapsed,
                }).execute()
            except Exception as e:
                record_swallowed("usage_log_insert", e)

        else:
            new_fail_count = job.get("fail_count", 0) + 1
            elapsed_str = status_reporter._format_elapsed(elapsed)
            if new_fail_count >= MAX_FAIL_COUNT:
                await set_job_status(
                    supabase, job_id, "paused",
                    fail_count=new_fail_count,
                    result_summary=result["summary"][:2000],
                )
                logger.warning(f"⏸ Job #{job_id} paused after {new_fail_count} failures")
                await notify(job_id, repo_name, "paused", f"Failed {new_fail_count}x in {elapsed_str}. Paused.")

                # BUG-017: push blocker event to cc-ihsanos inbox (ARCH-018/ARCH-028)
                try:
                    await supabase.table("agent_messages").insert({
                        "from_agent": "ralph_runner",
                        "to_agent": "cc-ihsanos",
                        "message_type": "blocker",
                        "subject": f"Job #{job_id} paused after {new_fail_count} failures: {job.get('description', '')[:80]}",
                        "body": f"{result['summary'][:800]}\n\nFailed {new_fail_count}/{MAX_FAIL_COUNT} attempts. Manual review required.",
                        "requires_response": True,
                    }).execute()
                except Exception as _e:
                    record_swallowed("bug017_paused_msg", _e)

                # ARCH-030: spawn dangerous-mode CC to self-diagnose this paused job.
                # Fire-and-forget — does not block the main loop.
                if _arch030_escalation_enabled():
                    try:
                        asyncio.create_task(
                            _spawn_escalation_session(
                                supabase=supabase,
                                job=job,
                                result_summary=result["summary"],
                                session_prompt=prompt_text,
                                repo_path=context["repo_path"],
                            ),
                            name=f"arch030_{job_id}",
                        )
                        logger.info(f"ARCH-030: escalation task spawned for job #{job_id}")
                    except Exception as _e:
                        record_swallowed("arch030_task_create", _e)

                # Notify Musa if this was a strategic decision that failed
                if job.get("triggered_by") == "strategic_decisions_poll":
                    import re as _re
                    _match = _re.match(r"\[([A-Z]+-\d+)\]", job.get("description", ""))
                    if _match:
                        try:
                            from telegram import Bot as _Bot
                            _bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                            _musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                            if _bot_token and _musa_id:
                                _bot = _Bot(token=_bot_token)
                                await notify_decision_complete(
                                    supabase, _match.group(1), job_id, False,
                                    f"Failed after {new_fail_count} attempts: {result['summary'][:300]}",
                                    _bot, _musa_id,
                                )
                        except Exception as _e:
                            logger.error(f"Strategic decision failure notify failed: {_e}")
                            record_swallowed("decision_notify_fail_max", _e)

                await status_reporter.report(
                    job=job,
                    result=result,
                    deploy_url=None,
                    elapsed_seconds=elapsed,
                    client_chat_id=client_chat_id,
                    supabase=supabase,
                )
            else:
                await set_job_status(
                    supabase, job_id, "queued",
                    fail_count=new_fail_count,
                    result_summary=result["summary"][:2000],
                )
                await notify(job_id, repo_name, "failed", f"Attempt {new_fail_count}/{MAX_FAIL_COUNT} failed ({elapsed_str}). Retrying.")
                logger.warning(
                    f"⚠️ Job #{job_id} failed (attempt {new_fail_count}/{MAX_FAIL_COUNT}), re-queued"
                )

            # Write partial work output for failed builds (ARCH-010, non-blocking)
            try:
                await _write_work_output(
                    supabase, job_id, repo_name,
                    build_spec=prompt_text[:50000],
                    cc_output_summary=result["summary"][:5000],
                    test_passed=False,
                    success=False,
                )
            except Exception as e:
                record_swallowed("work_output_write_failed", e)
            try:
                await build_audit.verify_work_output(supabase, job_id, repo_name)
            except Exception as e:
                record_swallowed("work_output_verify_failed", e)
            try:
                await _write_work_session(
                    supabase, job_id, repo_name,
                    triggered_by=job.get("triggered_by"),
                    session_prompt=prompt_text[:50000],
                    narrative=_build_narrative(job, "failed", result["summary"], None, elapsed),
                    outcome="failed",
                    duration_seconds=int(elapsed),
                )
            except Exception as e:
                record_swallowed("work_session_write_failed", e)

    except Exception as e:
        logger.exception(f"💥 Job #{job_id} crashed: {e}")
        new_fail_count = job.get("fail_count", 0) + 1
        status = "paused" if new_fail_count >= MAX_FAIL_COUNT else "queued"
        await set_job_status(
            supabase, job_id, status,
            fail_count=new_fail_count,
            result_summary=str(e)[:2000],
        )

        # Write crash work output (ARCH-010)
        try:
            await _write_work_output(
                supabase, job_id, repo_name,
                build_spec=prompt_text[:50000] if 'prompt_text' in locals() else None,
                cc_output_summary=str(e)[:5000],
                success=False,
            )
        except Exception as e2:
            record_swallowed("crash_work_output_write", e2)
        try:
            await build_audit.verify_work_output(supabase, job_id, repo_name)
        except Exception as e2:
            record_swallowed("crash_work_output_verify", e2)
        try:
            await _write_work_session(
                supabase, job_id, repo_name,
                triggered_by=job.get("triggered_by"),
                session_prompt=prompt_text[:50000] if 'prompt_text' in locals() else None,
                narrative=_build_narrative(job, "crashed", str(e), None, elapsed if 'elapsed' in locals() else None),
                outcome="crashed",
                duration_seconds=int(elapsed) if 'elapsed' in locals() else None,
            )
        except Exception as e2:
            record_swallowed("crash_work_session_write", e2)

        # ARCH-030: escalation for crashes too (only when job is now paused).
        if _arch030_escalation_enabled() and new_fail_count >= MAX_FAIL_COUNT:
            _rp = context["repo_path"] if "context" in locals() else ""  # type: ignore[name-defined]
            if _rp:
                try:
                    asyncio.create_task(
                        _spawn_escalation_session(
                            supabase=supabase,
                            job=job,
                            result_summary=str(e)[:1000],
                            session_prompt=prompt_text[:50000] if "prompt_text" in locals() else "",  # type: ignore[name-defined]
                            repo_path=_rp,
                        ),
                        name=f"arch030_crash_{job_id}",
                    )
                    logger.info(f"ARCH-030: escalation task spawned for crashed job #{job_id}")
                except Exception as _e2:
                    record_swallowed("arch030_crash_task", _e2)


async def start_webhook_server(supabase):
    """Start the webhook server for client bots."""
    from bot_manager import BotManager
    from webhook_server import create_webhook_app
    from message_dispatcher import dispatch

    bot_manager = BotManager()
    count = await bot_manager.load_all(supabase)

    if count > 0:
        await bot_manager.register_all_webhooks()
        logger.info(f"Registered webhooks for {count} client bots")

    # Create the dispatch function with supabase bound
    async def handle_message(client_bot, update_data):
        await dispatch(client_bot, update_data, supabase)

    app = await create_webhook_app(bot_manager, handle_message)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("WEBHOOK_PORT", "8443"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook server listening on port {port}")

    return bot_manager


async def main_loop():
    """Main orchestrator loop — parallel execution across repos, sequential within.

    Picks up to MAX_CONCURRENT_BUILDS jobs (one per repo) and runs them
    concurrently. Same-repo jobs run sequentially to avoid git conflicts.
    """
    logger.info(f"🚀 Wingmen Orchestrator starting (max {MAX_CONCURRENT_BUILDS} concurrent builds)")

    supabase = await get_supabase()
    logger.info("Connected to Supabase")

    # Start webhook server for client bots
    try:
        _bot_manager = await start_webhook_server(supabase)
        logger.info("Webhook server started")
        # Share bot_manager with cto_bot for onboarding
        try:
            from cto_bot import set_bot_manager
            set_bot_manager(_bot_manager)
        except Exception as e:
            record_swallowed("set_bot_manager_import", e)
    except Exception as e:
        logger.error(f"Webhook server failed to start: {e}")
        record_swallowed("webhook_server_start", e)

    zombie_count = await cleanup_zombie_jobs(supabase)
    if zombie_count:
        logger.info(f"Startup: cleaned {zombie_count} zombie running job(s)")

    recovery_counter = 0
    escalation_counter = 0
    heartbeat_counter = 0
    cleanup_counter = 0
    feature_health_counter = 0
    strategic_decisions_counter = 0
    qa_bridge_counter = 0
    drift_audit_counter = 0
    paused_job_counter = 0
    queue_stall_counter = 0
    swallowed_except_counter = 0
    uptime_monitor_counter = 0
    agent_messages_counter = 0
    pipeline_clock_counter = 0
    agent_watchdog_counter = 0
    dream_counter = 0
    ecosystem_frequent_counter = 0   # GATE 4 every 10 polls (~5 min)
    ecosystem_half_hour_counter = 0  # GATE 2 every 60 polls (~30 min)
    ecosystem_hourly_counter = 0     # GATE 1 every 120 polls (~60 min)
    archive_last_run: str | None = None  # "YYYY-MM-DD" — prevents double-runs in the 03:00h window
    running_tasks: dict[str, asyncio.Task] = {}  # repo_name -> Task

    while True:
        try:
            # Clean up finished tasks
            done_repos = [repo for repo, task in running_tasks.items() if task.done()]
            for repo in done_repos:
                task = running_tasks.pop(repo)
                # Surface any unhandled exceptions
                if task.exception():
                    logger.error(f"Task for {repo} failed: {task.exception()}")

            # Recover stale jobs every 10 polls (~5 min)
            recovery_counter += 1
            if recovery_counter >= 10:
                await recover_stale_jobs(supabase)
                recovery_counter = 0

            # Write heartbeat every 5 polls (~2.5 min)
            heartbeat_counter += 1
            if heartbeat_counter % 5 == 0:
                await write_orchestrator_heartbeat(supabase)

            # Check stale bug reports every 60 polls (~30 min)
            escalation_counter += 1
            if escalation_counter >= 60:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    if bot_token:
                        bot = Bot(token=bot_token)
                        await check_stale_bugs(supabase, bot)
                except Exception as e:
                    logger.error(f"Bug escalation check failed: {e}")
                    record_swallowed("bug_escalation_check", e)
                escalation_counter = 0

            # Check paused jobs every 60 polls (~30 min)
            paused_job_counter += 1
            if paused_job_counter >= 60:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    if bot_token:
                        bot = Bot(token=bot_token)
                        await check_paused_jobs(supabase, bot)
                except Exception as e:
                    logger.error(f"Paused job escalation check failed: {e}")
                    record_swallowed("paused_job_escalation", e)
                paused_job_counter = 0

            # Check queue stalls every 60 polls (~30 min)
            queue_stall_counter += 1
            if queue_stall_counter >= 60:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    if bot_token:
                        bot = Bot(token=bot_token)
                        await check_queue_stalls(supabase, bot)
                except Exception as e:
                    logger.error(f"Queue stall check failed: {e}")
                    record_swallowed("queue_stall_check", e)
                queue_stall_counter = 0

            # Clean up expired bot conversations every 60 polls (~30 min)
            cleanup_counter += 1
            if cleanup_counter >= 60:
                try:
                    await cleanup_expired_conversations(supabase)
                except Exception as e:
                    logger.error(f"Conversation cleanup failed: {e}")
                    record_swallowed("conversation_cleanup", e)
                cleanup_counter = 0

            # Relay live council messages to Musa's Telegram every poll (~30s).
            # Each new row in cto_council gets sent as it happens — Musa sees
            # the discussion in real-time and can /rule or /concur mid-stream.
            try:
                await relay_council_messages(supabase)
            except Exception as e:
                logger.error(f"Council relay task failed: {e}")
                record_swallowed("council_relay", e)

            # Summarize newly-ended council sessions every poll (~30s).
            # Fail-soft: logs errors but never blocks the main loop.
            try:
                await summarize_pending_sessions(supabase)
            except Exception as e:
                logger.error(f"Council summary task failed: {e}")
                record_swallowed("council_summary", e)

            # Pick up externally-inserted bug reports (from CI, E2E tests).
            # Every 60 polls (~30 min), same cadence as bug escalation.
            if escalation_counter == 30:
                try:
                    await poll_undiagnosed_bugs(supabase)
                except Exception as e:
                    logger.error(f"Bug poll task failed: {e}")
                    record_swallowed("bug_poll", e)

            # Autonomous council agent — responds as Claude Code when it's
            # our turn (last message was from Al-Mushtashir). Every poll.
            try:
                await run_council_agent(supabase)
            except Exception as e:
                logger.error(f"Council agent failed: {e}")
                record_swallowed("council_agent", e)

            # Council executor — every 2 polls (~60s). Needs faster polling
            # than other tasks because dry-run review has a 5-min timeout.
            try:
                await poll_executor(supabase)
            except Exception as e:
                logger.error(f"Council executor poll failed: {e}")
                record_swallowed("council_executor", e)

            # Strategic decisions poll — every 10 polls (~5 min).
            # ARCH-004: auto-notify when cai writes new decisions.
            # AUTOCC gate: poll_strategic_decisions auto-queues jobs from accepted
            # decisions; gated. poll_cai_review_requests only sends Telegram pings
            # for review-needed decisions; ungated (no job side effect).
            strategic_decisions_counter += 1
            if strategic_decisions_counter >= 10:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                    bot = Bot(token=bot_token) if (bot_token and musa_id) else None
                    if _autocc_poll_enabled():
                        if bot:
                            await poll_strategic_decisions(supabase, bot, musa_id)
                        else:
                            await poll_strategic_decisions(supabase)
                    if bot:
                        await poll_cai_review_requests(supabase, bot, musa_id)
                    else:
                        await poll_cai_review_requests(supabase)
                except Exception as e:
                    logger.error(f"Strategic decisions poll failed: {e}")
                    record_swallowed("strategic_decisions_poll", e)
                strategic_decisions_counter = 0

            # Agent messages poll — every 10 polls (~5 min).
            # TASK-041: routes CC→cai/musa messages to Telegram. Skips CC-to-CC.
            agent_messages_counter += 1
            if agent_messages_counter >= 10:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                    if bot_token and musa_id:
                        bot = Bot(token=bot_token)
                        await poll_agent_messages(supabase, bot, musa_id)
                    else:
                        await poll_agent_messages(supabase)
                except Exception as e:
                    logger.error(f"Agent messages poll failed: {e}")
                    record_swallowed("agent_messages_poll", e)
                agent_messages_counter = 0

            # Pipeline clock — every 10 polls (~5 min), but self-throttles to 24h.
            # TASK-042: increments days_clean for green bug_pipeline_readiness gates.
            # Cheap to call — does DB work only when 24h have elapsed since last tick.
            pipeline_clock_counter += 1
            if pipeline_clock_counter >= 10:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                    if bot_token and musa_id:
                        bot = Bot(token=bot_token)
                        await tick_pipeline_clock(supabase, bot, musa_id)
                    else:
                        await tick_pipeline_clock(supabase)
                except Exception as e:
                    logger.error(f"Pipeline clock tick failed: {e}")
                    record_swallowed("pipeline_clock", e)
                pipeline_clock_counter = 0

            # Agent watchdog — every 20 polls (~10 min).
            # ARCH-022 Layer 3: heartbeat staleness + check-in silence alerts.
            agent_watchdog_counter += 1
            if agent_watchdog_counter >= 20:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                    if bot_token and musa_id:
                        bot = Bot(token=bot_token)
                        await check_agent_health(supabase, bot, musa_id)
                    else:
                        await check_agent_health(supabase)
                except Exception as e:
                    logger.error(f"Agent watchdog failed: {e}")
                    record_swallowed("agent_watchdog", e)
                agent_watchdog_counter = 0

            # QA bridge — every 10 polls (~5 min).
            # Picks up qa_findings rows, deduplicates, bridges to bug_reports.
            qa_bridge_counter += 1
            if qa_bridge_counter >= 10:
                try:
                    await poll_qa_findings(supabase)
                except Exception as e:
                    logger.error(f"QA bridge poll failed: {e}")
                    record_swallowed("qa_bridge_poll", e)
                qa_bridge_counter = 0

            # Uptime monitor — every 10 polls (~5 min).
            uptime_monitor_counter += 1
            if uptime_monitor_counter >= 10:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                    if bot_token and musa_id:
                        bot = Bot(token=bot_token)
                        await poll_uptime(supabase, bot, musa_id)
                    else:
                        await poll_uptime(supabase)
                except Exception as e:
                    logger.error(f"Uptime monitor poll failed: {e}")
                    record_swallowed("uptime_monitor", e)
                uptime_monitor_counter = 0

            # Feature health signal — every 60 polls (~30 min).
            # Scans launchctl + logs + static files, writes advisory
            # health_signal back to wingmen_features. Stage promotion
            # remains 100% manual per CTO Council session 2 option 4.
            feature_health_counter += 1
            if feature_health_counter >= 60:
                try:
                    await collect_feature_health(supabase)
                except Exception as e:
                    logger.error(f"Feature health signal task failed: {e}")
                    record_swallowed("feature_health", e)
                feature_health_counter = 0

            # Semantic drift audit — every 60 polls (~30 min).
            drift_audit_counter += 1
            if drift_audit_counter >= 60:
                try:
                    await semantic_drift.run_drift_audit(supabase)
                except Exception as e:
                    logger.error(f"Semantic drift audit failed: {e}")
                    record_swallowed("drift_audit", e)
                drift_audit_counter = 0

            # Swallowed-exception escalation — every 60 polls (~30 min).
            swallowed_except_counter += 1
            if swallowed_except_counter >= 60:
                try:
                    from telegram import Bot
                    from nervous_system.swallowed_except_harness import check_swallowed_escalation
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    if bot_token:
                        bot = Bot(token=bot_token)
                        await check_swallowed_escalation(supabase, bot)
                except Exception as e:
                    logger.error(f"Swallowed-except escalation check failed: {e}")
                swallowed_except_counter = 0

            # Archive completed jobs + terminal decisions once daily at 03:00 SGT.
            # Counter-based time check: runs once per 03:xx hour, never twice.
            _sgt = timezone(timedelta(hours=8))
            _now_sgt = datetime.now(_sgt)
            if _now_sgt.hour == 3 and _now_sgt.strftime("%Y-%m-%d") != archive_last_run:
                try:
                    await run_archive(supabase)
                except Exception as e:
                    logger.error(f"Archive task failed: {e}")
                    record_swallowed("archive", e)
                archive_last_run = _now_sgt.strftime("%Y-%m-%d")

            # Wingmen Dream — memory consolidation every 12 polls (~6 min).
            # Gates inside run_dream() prevent actual Haiku calls more often
            # than once every 6 hours and only when activity threshold is met.
            dream_counter += 1
            if dream_counter >= 12:
                try:
                    await run_dream(supabase)
                except Exception as e:
                    logger.error(f"Wingmen Dream failed: {e}")
                    record_swallowed("wingmen_dream", e)
                dream_counter = 0

            # Ecosystem Auditor — ARCH-023 self-maintaining governance gates.
            # GATE 4 (CC-UPDATE classification) every 10 polls (~5 min).
            ecosystem_frequent_counter += 1
            if ecosystem_frequent_counter >= 10:
                try:
                    await run_frequent_gates(supabase)
                except Exception as e:
                    logger.error(f"Ecosystem frequent gates failed: {e}")
                    record_swallowed("ecosystem_gate4", e)
                ecosystem_frequent_counter = 0

            # GATE 2 (ship verify) every 60 polls (~30 min).
            ecosystem_half_hour_counter += 1
            if ecosystem_half_hour_counter >= 60:
                try:
                    await run_half_hour_gates(supabase)
                except Exception as e:
                    logger.error(f"Ecosystem half-hour gates failed: {e}")
                    record_swallowed("ecosystem_gate2", e)
                ecosystem_half_hour_counter = 0

            # GATE 1 (challenge flip) every 120 polls (~60 min).
            ecosystem_hourly_counter += 1
            if ecosystem_hourly_counter >= 120:
                try:
                    await run_hourly_gates(supabase)
                except Exception as e:
                    logger.error(f"Ecosystem hourly gates failed: {e}")
                    record_swallowed("ecosystem_gate1", e)
                ecosystem_hourly_counter = 0

            # GATES 3 + 7 (daily at 06:00 / 04:00 SGT) — every poll, time-gated inside.
            # GATE 6 (contradiction, every 6h) — every poll, time-gated inside.
            try:
                _bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                _musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                if _bot_token and _musa_id:
                    from telegram import Bot as _TGBot
                    _bot = _TGBot(token=_bot_token)
                    await run_daily_gates(supabase, _bot, _musa_id)
                    await run_six_hour_gates(supabase, _bot, _musa_id)
                else:
                    await run_daily_gates(supabase)
                    await run_six_hour_gates(supabase)
            except Exception as e:
                logger.error(f"Ecosystem daily/6h gates failed: {e}")
                record_swallowed("ecosystem_daily_gates", e)

            # How many slots available?
            available = MAX_CONCURRENT_BUILDS - len(running_tasks)
            running_repos = set(running_tasks.keys())

            # AUTOCC gate: skip the job picker entirely when disabled. The
            # orchestrator continues running heartbeat / monitoring / bug-pipeline
            # polls but does not claim queued jobs. Existing in-flight jobs
            # (running_tasks) finish naturally.
            if available > 0 and _autocc_poll_enabled():
                jobs = await pick_next_jobs(supabase, running_repos, available)
                for job in jobs:
                    repo = job["repo_name"]
                    task = asyncio.create_task(
                        run_job(supabase, job),
                        name=f"job_{job['id']}_{repo}",
                    )
                    running_tasks[repo] = task
                    logger.info(f"▶ Launched job #{job['id']} for {repo} ({len(running_tasks)}/{MAX_CONCURRENT_BUILDS} slots)")

            if not running_tasks:
                logger.debug("No active jobs, sleeping...")

        except Exception as e:
            logger.exception(f"Loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


async def _shutdown(loop: asyncio.AbstractEventLoop, signal_name: str) -> None:
    """Cancel in-flight tasks and exit cleanly on SIGTERM/SIGINT."""
    logger.info(f"Received {signal_name} — starting graceful shutdown")
    tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Graceful shutdown complete")
    loop.stop()


def _cancel_pending_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel and await any tasks still pending before ``loop.close()``.

    Called from the ``finally`` block after ``run_until_complete`` returns so
    leftover tasks (e.g. the ``_shutdown`` task itself, or tasks spawned during
    cancellation cleanup) don't emit "Task was destroyed but it is pending".
    """
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    except RuntimeError:
        return
    if not pending:
        return
    for task in pending:
        task.cancel()
    try:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception as e:
        logger.debug(f"_cancel_pending_tasks swallowed: {e}")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    import signal as _signal
    for sig in (_signal.SIGTERM, _signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.ensure_future(_shutdown(loop, s.name), loop=loop),
        )
    try:
        loop.run_until_complete(main_loop())
    finally:
        _cancel_pending_tasks(loop)
        loop.close()
