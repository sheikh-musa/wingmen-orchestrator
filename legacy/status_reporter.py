"""Updates STATUS.md and notifies Musa via Telegram."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from context_loader import get_repo_config
from notification_router import get_chat_id

logger = logging.getLogger("wingmen.status")

SGT = timezone(timedelta(hours=8))
TELEGRAM_API = "https://api.telegram.org"


_DECISION_REF_PATTERN = re.compile(r"\[([A-Z]+-\d+)\]")


async def _human_title(supabase, description: str, fallback_repo: str) -> str:
    """Storytelling mode: turn '[TASK-022] Re-measure BUG-005 hydration...'
    into 'Hydration re-measurement (ihsanos)'. Falls back to repo + first
    clause of description if no decision_ref or lookup fails.
    """
    if supabase is None:
        return f"{fallback_repo}: {description[:60]}"
    match = _DECISION_REF_PATTERN.search(description)
    if not match:
        return f"{fallback_repo}: {description[:60]}"
    ref = match.group(1)
    try:
        result = await supabase.table("strategic_decisions").select(
            "title"
        ).eq("decision_ref", ref).limit(1).single().execute()
        title = result.data.get("title", "") if result.data else ""
        # Strip common prefixes and take first clause
        for prefix in (f"{ref}: ", f"[{ref}] "):
            if title.startswith(prefix):
                title = title[len(prefix):]
                break
        # First sentence or clause, capped at 70 chars
        short = title.split(" — ")[0].split(": ")[0].strip()[:70]
        return f"{short} ({fallback_repo})" if short else f"{fallback_repo}: {description[:60]}"
    except Exception:
        return f"{fallback_repo}: {description[:60]}"


_PHASE_VERBS = {
    "picked": "Starting",
    "context": "Loading context",
    "spec": "Planning",
    "claude": "Building",
    "deploy": "Deploying",
    "failed": "Failed",
    "paused": "Paused",
}


async def notify_progress(
    job_id: int,
    repo_name: str,
    phase: str,
    detail: str = "",
    client_chat_id: str | None = None,
    supabase=None,
) -> None:
    """Send a lightweight Telegram progress update to admin + client.

    Storytelling mode: uses the decision title, not [TASK-XXX] refs.
    BUG-002: Dedups via notification_log.dedup_key to prevent duplicate sends.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    admin_id = get_chat_id("cto")
    if not token:
        return

    # BUG-002 dedup: check if we've already sent this (job_id, phase)
    dedup_key = f"job:{job_id}:{phase}"
    if supabase is not None:
        try:
            existing = await supabase.table("notification_log").select("id").eq(
                "dedup_key", dedup_key
            ).limit(1).execute()
            if existing.data:
                logger.info(f"Skipping duplicate notification: {dedup_key}")
                return
        except Exception as e:
            logger.warning(f"Dedup check failed for {dedup_key}: {e}")

    # Storytelling mode: look up the decision title from the job description
    # (which still carries [TASK-XXX] for audit).
    description = detail if phase == "picked" else ""
    # notify_progress gets `detail` which varies by phase; fetch job description
    # from DB for consistent title lookup.
    try:
        job_row = await supabase.table("jobs").select("description").eq(
            "id", job_id
        ).single().execute() if supabase else None
        if job_row and job_row.data:
            description = job_row.data.get("description", description)
    except Exception:
        pass

    human = await _human_title(supabase, description, repo_name)

    icons = {
        "picked": "\u25b6\ufe0f",     # ▶️
        "context": "\U0001f4c2",       # 📂
        "spec": "\U0001f4dd",          # 📝
        "claude": "\U0001f916",        # 🤖
        "deploy": "\U0001f680",        # 🚀
        "failed": "\u26a0\ufe0f",      # ⚠️
        "paused": "\u23f8\ufe0f",      # ⏸️
    }
    icon = icons.get(phase, "\U0001f504")  # 🔄
    verb = _PHASE_VERBS.get(phase, phase.capitalize())

    if phase in ("failed", "paused") and detail:
        # Errors need the detail visible
        msg = f"{icon} {verb}: {human}\n\n{detail[:400]}"
    elif detail and phase != "picked":
        msg = f"{icon} {verb}: {human}\n{detail[:200]}"
    else:
        msg = f"{icon} {verb}: {human}"

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    # Send to all recipients (admin always, client if set)
    recipients = set()
    if admin_id:
        recipients.add(admin_id)
    if client_chat_id:
        recipients.add(client_chat_id)

    sent_any = False
    async with httpx.AsyncClient(timeout=10) as client:
        for chat_id in recipients:
            try:
                await client.post(url, json={"chat_id": chat_id, "text": msg})
                sent_any = True
            except Exception as e:
                logger.warning(f"Progress notify to {chat_id} failed: {e}")

    # Log to notification_log with dedup_key to prevent future duplicates
    if sent_any and supabase is not None:
        try:
            await supabase.table("notification_log").insert({
                "source": "job_progress",
                "decision_ref": None,
                "channel": "telegram",
                "recipient": admin_id or "unknown",
                "message_text": msg,
                "dedup_key": dedup_key,
            }).execute()
        except Exception as e:
            # Unique constraint violation = someone else logged it first, harmless
            logger.debug(f"Notification log insert skipped (likely dedup race): {e}")


async def report(
    job: dict,
    result: dict,
    deploy_url: str | None,
    elapsed_seconds: float,
    client_chat_id: str | None = None,
    supabase=None,
) -> None:
    """Update STATUS.md in repo and send Telegram notification."""
    config = get_repo_config(job["repo_name"])
    repo_path = Path(os.path.expanduser(config["local_path"]))

    # Determine build status
    if result["success"]:
        build_status = "green"
        status_emoji = "\u2705"
    else:
        build_status = "red"
        status_emoji = "\u274c"

    now = datetime.now(SGT).strftime("%Y-%m-%d %H:%M SGT")
    elapsed = _format_elapsed(elapsed_seconds)

    # Update repo STATUS.md
    await _update_status_md(repo_path, job, build_status, deploy_url, now)

    # Git commit the STATUS.md change
    await _git_commit_status(repo_path, job["id"])

    # Update orchestrator STATUS.md
    await _update_orch_status(job, build_status, deploy_url, now, elapsed)

    # Send Telegram notification (BUG-002: dedup via supabase)
    await _send_telegram(job, build_status, status_emoji, deploy_url, elapsed, client_chat_id, supabase)

    # Send screenshot of live production URL (not preview URL)
    if result["success"]:
        prod_url = config.get("deploy_url")
        if prod_url and prod_url != "FILL_IN":
            await _send_deploy_screenshot(job, prod_url, client_chat_id)


async def _update_status_md(
    repo_path: Path,
    job: dict,
    build_status: str,
    deploy_url: str | None,
    timestamp: str,
) -> None:
    """Update STATUS.md by preserving all existing content and only touching
    the '## Recent Jobs (auto-tracked)' section at the bottom.

    Never rewrites the file. If the marker section is missing, appends it.
    This prevents the regression where orchestrator jobs would overwrite
    hand-written project context with a generic template.
    """
    status_file = repo_path / "STATUS.md"

    marker = "## Recent Jobs (auto-tracked)"
    deploy_display = deploy_url or "N/A"
    job_line = f"| #{job['id']} | {job['description'][:80]} | {build_status} | {deploy_display} |"
    header = "| Job | Description | Status | Deploy |\n|-----|-------------|--------|--------|"

    existing = status_file.read_text() if status_file.exists() else f"# {job['repo_name']} STATUS\n\n(no hand-written content)\n"

    if marker in existing:
        # Update the marker section: keep everything before, rebuild the section
        before, _, tail = existing.partition(marker)
        # Extract existing job rows from the tail (lines starting with "| #")
        existing_rows = [
            line for line in tail.splitlines()
            if line.startswith("| #") and "Job" not in line.split("|")[1]
        ][:9]  # keep last 9 + this one = 10 total
        new_section = f"{marker}\n\nLast Updated: {timestamp}\n\n{header}\n{job_line}\n" + "\n".join(existing_rows) + "\n"
        new_content = before.rstrip() + "\n\n" + new_section
    else:
        # Append the marker section — don't touch existing content
        new_section = f"\n\n---\n\n{marker}\n\nLast Updated: {timestamp}\n\n{header}\n{job_line}\n"
        new_content = existing.rstrip() + new_section

    status_file.write_text(new_content)
    logger.info(f"Updated STATUS.md Recent Jobs for {job['repo_name']}")


async def _update_orch_status(
    job: dict,
    build_status: str,
    deploy_url: str | None,
    timestamp: str,
    elapsed: str,
) -> None:
    """Update the orchestrator's own STATUS.md with job results."""
    orch_status = Path(__file__).parent / "STATUS.md"
    try:
        existing = orch_status.read_text() if orch_status.exists() else ""

        status_emoji = "green" if build_status == "green" else "red"
        deploy_line = deploy_url or "N/A"
        job_line = f"- [{status_emoji}] Job #{job['id']}: {job['repo_name']} — {job['description']} ({elapsed}, deploy: {deploy_line})"

        # Update "Active Jobs" → remove this job
        # Update "Completed (Last 5)" → prepend this job
        if "## Completed (Last 5)" in existing:
            parts = existing.split("## Completed (Last 5)")
            after = parts[1]
            # Get existing completed lines
            completed_section = after.split("##")[0].strip()
            completed_lines = [l for l in completed_section.splitlines() if l.strip() and l.strip() != "- none"]
            completed_lines.insert(0, job_line)
            completed_lines = completed_lines[:5]  # keep last 5

            new_completed = "\n".join(completed_lines)
            after_rest = "##".join(after.split("##")[1:]) if "##" in after else ""
            new_content = parts[0] + f"## Completed (Last 5)\n{new_completed}\n\n" + ("## " + after_rest if after_rest else "")
        else:
            # No completed section, append
            new_content = existing + f"\n## Completed (Last 5)\n{job_line}\n"

        # Update timestamp
        if "Last Updated:" in new_content:
            lines = new_content.splitlines()
            lines = [f"Last Updated: {timestamp}" if l.startswith("Last Updated:") else l for l in lines]
            new_content = "\n".join(lines)

        orch_status.write_text(new_content)
        logger.info("Updated orchestrator STATUS.md")

        # Commit the orchestrator's own STATUS.md change. Without this,
        # every job leaves ~/wingmen/orchestrator with a dirty tree, which
        # TASK-027's pre-flight clean-tree check then rejects on the NEXT
        # orchestrator self-job. TASK-033, TASK-034, TASK-037 all got
        # blocked by this on 2026-04-14 before the fix.
        orch_dir = Path(__file__).parent
        if (orch_dir / ".git").exists():
            proc = await asyncio.create_subprocess_exec(
                "git", "add", "STATUS.md",
                cwd=str(orch_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", f"chore: orch STATUS.md [job_{job['id']}]",
                cwd=str(orch_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            # Non-zero exit is fine (e.g., nothing to commit for self-jobs
            # where _git_commit_status already captured STATUS.md).
    except Exception as e:
        logger.warning(f"Failed to update orchestrator STATUS.md: {e}")


async def _git_commit_status(repo_path: Path, job_id: int) -> None:
    if not (repo_path / ".git").exists():
        logger.warning(f"No .git in {repo_path}, skipping commit")
        return

    process = await asyncio.create_subprocess_exec(
        "git", "add", "STATUS.md",
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()

    process = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", f"chore: update STATUS.md [job_{job_id}]",
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0:
        logger.info(f"Committed STATUS.md for job_{job_id}")
    else:
        logger.warning(f"Git commit failed: {stderr.decode(errors='replace')}")


async def _send_telegram(
    job: dict,
    build_status: str,
    status_emoji: str,
    deploy_url: str | None,
    elapsed: str,
    client_chat_id: str | None = None,
    supabase=None,
) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    admin_id = get_chat_id("cto")

    if not token:
        logger.warning("Telegram token not set, skipping notification")
        return

    # BUG-002 dedup: check if we've already sent the complete notification for this job
    dedup_key = f"job:{job['id']}:complete"
    if supabase is not None:
        try:
            existing = await supabase.table("notification_log").select("id").eq(
                "dedup_key", dedup_key
            ).limit(1).execute()
            if existing.data:
                logger.info(f"Skipping duplicate notification: {dedup_key}")
                return
        except Exception as e:
            logger.warning(f"Dedup check failed for {dedup_key}: {e}")

    # Storytelling mode: use decision title, not [TASK-XXX] ref
    human = await _human_title(supabase, job.get("description", ""), job["repo_name"])
    landed_verb = "Landed" if build_status == "green" else "Broke"
    deploy_line = f"\U0001f4e6 Live: {deploy_url}\n" if deploy_url else ""
    message = (
        f"{status_emoji} {landed_verb}: {human}\n"
        f"{deploy_line}"
        f"\u23f1 {elapsed}"
    ).strip()

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    recipients = set()
    if admin_id:
        recipients.add(admin_id)
    if client_chat_id:
        recipients.add(client_chat_id)

    sent_any = False
    async with httpx.AsyncClient(timeout=10) as client:
        for chat_id in recipients:
            try:
                resp = await client.post(url, json={"chat_id": chat_id, "text": message})
                resp.raise_for_status()
                logger.info(f"Telegram notification sent for job_{job['id']} to {chat_id}")
                sent_any = True
            except Exception as e:
                logger.error(f"Telegram send to {chat_id} failed: {e}")

    # Log with dedup_key to prevent future duplicates
    if sent_any and supabase is not None:
        try:
            await supabase.table("notification_log").insert({
                "source": "job_complete",
                "decision_ref": None,
                "channel": "telegram",
                "recipient": admin_id or "unknown",
                "message_text": message,
                "dedup_key": dedup_key,
            }).execute()
        except Exception as e:
            logger.debug(f"Notification log insert skipped: {e}")


async def _send_deploy_screenshot(
    job: dict,
    deploy_url: str,
    client_chat_id: str | None = None,
) -> None:
    """Take a screenshot of the deployed site and send to admin + client."""
    import tempfile

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    admin_id = get_chat_id("cto")
    if not token:
        return

    screenshot_path = os.path.join(tempfile.gettempdir(), f"deploy_{job['id']}.png")

    try:
        # Wait a bit for Vercel to finish deploying
        await asyncio.sleep(10)

        proc = await asyncio.create_subprocess_exec(
            "npx", "playwright", "screenshot", deploy_url, screenshot_path,
            "--viewport-size", "375,812",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0 or not Path(screenshot_path).exists():
            logger.warning(f"Screenshot failed for {deploy_url}")
            return

        recipients = set()
        if admin_id:
            recipients.add(admin_id)
        if client_chat_id:
            recipients.add(client_chat_id)

        caption = f"\U0001f4f1 {job['repo_name']} — deployed\n{deploy_url}"

        async with httpx.AsyncClient(timeout=30) as client:
            for chat_id in recipients:
                try:
                    with open(screenshot_path, "rb") as f:
                        await client.post(
                            f"{TELEGRAM_API}/bot{token}/sendPhoto",
                            data={"chat_id": chat_id, "caption": caption},
                            files={"photo": ("screenshot.png", f, "image/png")},
                        )
                except Exception as e:
                    logger.warning(f"Screenshot send to {chat_id} failed: {e}")

    except Exception as e:
        logger.warning(f"Deploy screenshot failed: {e}")
    finally:
        try:
            os.unlink(screenshot_path)
        except Exception:
            pass


def _format_elapsed(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
