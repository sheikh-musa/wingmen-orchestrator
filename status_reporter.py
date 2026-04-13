"""Updates STATUS.md and notifies Musa via Telegram."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from context_loader import get_repo_config

logger = logging.getLogger("wingmen.status")

SGT = timezone(timedelta(hours=8))
TELEGRAM_API = "https://api.telegram.org"


async def notify_progress(
    job_id: int,
    repo_name: str,
    phase: str,
    detail: str = "",
    client_chat_id: str | None = None,
    supabase=None,
) -> None:
    """Send a lightweight Telegram progress update to admin + client.

    BUG-002: Dedups via notification_log.dedup_key to prevent duplicate sends
    when orchestrator restarts or jobs get re-picked up.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    admin_id = os.environ.get("MUSA_TELEGRAM_ID")
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

    msg = f"{icon} Job #{job_id} [{repo_name}]\n{phase.upper()}: {detail}" if detail else f"{icon} Job #{job_id} [{repo_name}] — {phase}"

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
    status_file = repo_path / "STATUS.md"

    # Build fresh status block
    deploy_line = f"Deploy: {deploy_url}" if deploy_url else "Deploy: N/A"
    content = f"""# {job['repo_name']} STATUS

Last Updated: {timestamp}
Build Status: {build_status}
{deploy_line}

## Last Completed Job
- Job #{job['id']}: {job['description']}

## Result Summary
{job.get('result_summary', 'N/A')}
"""

    # Preserve existing sections if STATUS.md exists
    if status_file.exists():
        existing = status_file.read_text()
        # Extract "Next Up" section if present
        if "## Next Up" in existing:
            next_up = existing.split("## Next Up")[1].split("##")[0].strip()
            content += f"\n## Previous Next Up\n{next_up}\n"

    status_file.write_text(content)
    logger.info(f"Updated STATUS.md for {job['repo_name']}")


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
    admin_id = os.environ.get("MUSA_TELEGRAM_ID")

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

    deploy_line = f"\U0001f4e6 Deploy: {deploy_url}\n" if deploy_url else ""
    message = (
        f"{status_emoji} {job['repo_name']} — {job['description']}\n"
        f"{deploy_line}"
        f"\U0001f4cb Status: {build_status}\n"
        f"\u23f1 Duration: {elapsed}"
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
    admin_id = os.environ.get("MUSA_TELEGRAM_ID")
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
