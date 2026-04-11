"""Wingmen Orchestrator — main async worker loop.

Polls Supabase jobs table every 30s, picks highest priority queued job,
runs it through the build pipeline, and reports results.
"""

from __future__ import annotations

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
from nervous_system.bug_escalation import check_stale_bugs
from nervous_system.conversation_cleanup import cleanup_expired_conversations
from nervous_system.council_summary import summarize_pending_sessions
from heartbeat import write_orchestrator_heartbeat

# ── Setup ────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "orch.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("wingmen.orch")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
MAX_FAIL_COUNT = int(os.environ.get("MAX_FAIL_COUNT", "3"))
STALE_JOB_MINUTES = int(os.environ.get("STALE_JOB_MINUTES", "120"))
MAX_CONCURRENT_BUILDS = int(os.environ.get("MAX_CONCURRENT_BUILDS", "3"))


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
    return None


async def run_job(supabase, job: dict) -> None:
    """Execute the full build pipeline for a single job."""
    job_id = job["id"]
    repo_name = job["repo_name"]
    start_time = time.monotonic()

    logger.info(f"▶ Starting job #{job_id}: {repo_name} — {job['description']}")

    client_chat_id = await _resolve_client_chat_id(supabase, job)

    async def notify(jid, rname, phase, detail=""):
        await status_reporter.notify_progress(jid, rname, phase, detail, client_chat_id)

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
        except Exception:
            pass

        # 3. Generate spec prompt
        prompt_text = await spec_generator.generate_spec(job, context)
        logger.info(f"  Spec generated ({len(prompt_text)} chars)")
        await notify(job_id, repo_name, "spec", f"Generated build spec ({len(prompt_text)} chars)")

        # 4. Run Claude CLI
        await notify(job_id, repo_name, "claude", "Building...")
        result = await ralph_runner.run_claude(
            repo_path=context["repo_path"],
            prompt_text=prompt_text,
            job_id=job_id,
            repo_name=repo_name,
            supabase=supabase,
        )
        logger.info(f"  Claude done: success={result['success']}")

        elapsed = time.monotonic() - start_time

        if result["success"]:
            # 5. Git commit + push
            try:
                await notify(job_id, repo_name, "deploy", "Pushing changes...")
                await _git_push(context["repo_path"], job_id, job["description"])
            except Exception as e:
                logger.warning(f"  Git push failed: {e}")

            # 6. Deploy
            deploy_result = {"deployed": False, "url": None}
            try:
                deploy_result = await deploy_manager.deploy(repo_name)
                if deploy_result["deployed"]:
                    await notify(job_id, repo_name, "deploy", f"Live at {deploy_result['url']}")
                    logger.info(f"  Deployed → {deploy_result['url']}")
            except Exception as e:
                logger.error(f"  Deploy failed: {e}")

            # 7. Report success
            await status_reporter.report(
                job=job,
                result=result,
                deploy_url=deploy_result.get("url"),
                elapsed_seconds=elapsed,
                client_chat_id=client_chat_id,
            )
            await set_job_status(
                supabase, job_id, "completed",
                result_summary=result["summary"][:2000],
            )
            logger.info(f"✅ Job #{job_id} completed in {elapsed:.0f}s")

            # Log usage
            try:
                await supabase.table("usage_log").insert({
                    "client_id": job.get("client_id"),
                    "action_type": "build_completed",
                    "repo_name": repo_name,
                    "duration_seconds": elapsed,
                }).execute()
            except Exception:
                pass

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
                await status_reporter.report(
                    job=job,
                    result=result,
                    deploy_url=None,
                    elapsed_seconds=elapsed,
                    client_chat_id=client_chat_id,
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

    except Exception as e:
        logger.exception(f"💥 Job #{job_id} crashed: {e}")
        new_fail_count = job.get("fail_count", 0) + 1
        status = "paused" if new_fail_count >= MAX_FAIL_COUNT else "queued"
        await set_job_status(
            supabase, job_id, status,
            fail_count=new_fail_count,
            result_summary=str(e)[:2000],
        )


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
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Webhook server failed to start: {e}")

    recovery_counter = 0
    escalation_counter = 0
    heartbeat_counter = 0
    cleanup_counter = 0
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
                escalation_counter = 0

            # Clean up expired bot conversations every 60 polls (~30 min)
            cleanup_counter += 1
            if cleanup_counter >= 60:
                try:
                    await cleanup_expired_conversations(supabase)
                except Exception as e:
                    logger.error(f"Conversation cleanup failed: {e}")
                cleanup_counter = 0

            # Summarize newly-ended council sessions every poll (~30s).
            # Fail-soft: logs errors but never blocks the main loop.
            try:
                await summarize_pending_sessions(supabase)
            except Exception as e:
                logger.error(f"Council summary task failed: {e}")

            # How many slots available?
            available = MAX_CONCURRENT_BUILDS - len(running_tasks)
            running_repos = set(running_tasks.keys())

            if available > 0:
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


if __name__ == "__main__":
    asyncio.run(main_loop())
