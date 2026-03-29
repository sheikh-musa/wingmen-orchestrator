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

import context_loader
import spec_generator
import ralph_runner
import deploy_manager
import status_reporter

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

POLL_INTERVAL = 30  # seconds
MAX_FAIL_COUNT = 3


async def get_supabase():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return await acreate_client(url, key)


async def pick_next_job(supabase) -> dict | None:
    """Pick the highest priority queued job."""
    result = await (
        supabase.table("jobs")
        .select("*")
        .eq("status", "queued")
        .order("priority", desc=False)
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


async def set_job_status(supabase, job_id: int, status: str, **extra):
    """Update job status and any extra fields."""
    update = {"status": status, "updated_at": "now()"}
    update.update(extra)
    await supabase.table("jobs").update(update).eq("id", job_id).execute()


async def _git_push(repo_path: str, job_id: int, description: str) -> None:
    """Stage, commit, and push changes made by Claude CLI."""
    async def _run(cmd):
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    # Check if there are changes
    rc, out, _ = await _run(["git", "status", "--porcelain"])
    if not out.strip():
        logger.info(f"  No changes to commit for job #{job_id}")
        return

    # Stage all changes
    await _run(["git", "add", "-A"])

    # Commit
    msg = f"feat: {description[:100]} [job_{job_id}]"
    rc, out, err = await _run(["git", "commit", "-m", msg])
    if rc != 0:
        logger.warning(f"  Git commit failed: {err}")
        return

    # Push
    rc, out, err = await _run(["git", "push"])
    if rc != 0:
        logger.warning(f"  Git push failed: {err}")
        return

    logger.info(f"  Committed and pushed: {msg}")


async def run_job(supabase, job: dict) -> None:
    """Execute the full build pipeline for a single job."""
    job_id = job["id"]
    repo_name = job["repo_name"]
    start_time = time.monotonic()

    logger.info(f"▶ Starting job #{job_id}: {repo_name} — {job['description']}")
    await set_job_status(supabase, job_id, "running")

    # Resolve client chat_id for notifications
    client_chat_id = None
    if job.get("client_id"):
        try:
            client_result = await supabase.table("clients").select("telegram_chat_id").eq(
                "id", job["client_id"]
            ).limit(1).execute()
            if client_result.data:
                client_chat_id = client_result.data[0].get("telegram_chat_id")
        except Exception:
            pass

    async def notify(jid, rname, phase, detail=""):
        await status_reporter.notify_progress(jid, rname, phase, detail, client_chat_id)

    try:
        # 1. Load context
        await notify(job_id, repo_name, "picked", job["description"])
        context = await context_loader.load_context(repo_name, supabase)
        logger.info(f"  Context loaded for {repo_name}")
        await notify(job_id, repo_name, "context", "Loaded CLAUDE.md, STATUS.md, memory")

        # 2. Generate spec prompt
        prompt_text = await spec_generator.generate_spec(job, context)
        logger.info(f"  Spec generated ({len(prompt_text)} chars)")
        await notify(job_id, repo_name, "spec", f"Generated build spec ({len(prompt_text)} chars)")

        # 3. Run Claude CLI
        await notify(job_id, repo_name, "claude", "Claude Code running...")
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
            # 4. Git commit + push
            try:
                await notify(job_id, repo_name, "deploy", "Committing and pushing changes...")
                await _git_push(context["repo_path"], job_id, job["description"])
                logger.info(f"  Pushed to remote")
            except Exception as e:
                logger.warning(f"  Git push failed: {e}")

            # 5. Deploy
            deploy_result = {"deployed": False, "url": None}
            try:
                deploy_result = await deploy_manager.deploy(repo_name)
                if deploy_result["deployed"]:
                    await notify(job_id, repo_name, "deploy", f"Live at {deploy_result['url']}")
                    logger.info(f"  Deployed → {deploy_result['url']}")
            except Exception as e:
                logger.error(f"  Deploy failed: {e}")

            # 5. Report success
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

        else:
            # Increment fail count
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
                )
            else:
                await set_job_status(
                    supabase, job_id, "queued",
                    fail_count=new_fail_count,
                    result_summary=result["summary"][:2000],
                )
                await notify(job_id, repo_name, "failed", f"Attempt {new_fail_count}/{MAX_FAIL_COUNT} failed ({elapsed_str}). Re-queued.")
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


async def main_loop():
    """Main orchestrator loop — poll, pick, run, repeat."""
    logger.info("🚀 Wingmen Orchestrator starting")

    supabase = await get_supabase()
    logger.info("Connected to Supabase")

    while True:
        try:
            job = await pick_next_job(supabase)
            if job:
                await run_job(supabase, job)
            else:
                logger.debug("No queued jobs, sleeping...")
        except Exception as e:
            logger.exception(f"Loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
