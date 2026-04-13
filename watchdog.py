"""Wingmen Watchdog — monitors bot + orchestrator health.

Runs as a separate LaunchAgent. Checks every 60s if services are alive.
Alerts Musa via Telegram if anything is down.
Sets bot description to "offline" so clients see status before messaging.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "watchdog.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("wingmen.watchdog")

from notification_router import get_chat_id

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

CHECK_INTERVAL = 60  # seconds
_last_bot_status = True
_last_orch_status = True
_alerted = False


async def check_bot_alive() -> bool:
    """Check if the Telegram bot is responding."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{TELEGRAM_API}/getMe")
            return resp.status_code == 200
    except Exception:
        return False


async def check_orchestrator_alive() -> bool:
    """Check if the orchestrator process is running."""
    proc = await asyncio.create_subprocess_exec(
        "launchctl", "list",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode()
    # Check for running process (exit code 0 = running)
    for line in output.splitlines():
        if "dev.wingmen.orchestrator" in line:
            parts = line.split()
            # PID is first column, exit code is second
            return parts[1] == "0" or parts[0] != "-"
    return False


async def set_bot_description(online: bool) -> None:
    """Update bot description so clients see status before messaging."""
    if not TELEGRAM_TOKEN:
        return

    if online:
        desc = "Your AI project assistant from Wingmen. Just send a message to get started!"
    else:
        desc = "I'm temporarily offline for maintenance. I'll be back shortly and will respond to your messages then!"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{TELEGRAM_API}/setMyDescription",
                json={"description": desc},
            )
            # Also set short description (shown in profile)
            short = "Online — ready to help!" if online else "Temporarily offline — back shortly"
            await client.post(
                f"{TELEGRAM_API}/setMyShortDescription",
                json={"short_description": short},
            )
    except Exception as e:
        logger.warning(f"Failed to set bot description: {e}")


async def alert_admin(message: str) -> None:
    """Send alert to ops group (or Musa DM as fallback)."""
    chat_id = get_chat_id("ops")
    if not TELEGRAM_TOKEN or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": message},
            )
    except Exception:
        pass


async def try_restart_service(service: str) -> bool:
    """Attempt to restart a LaunchAgent service."""
    try:
        uid = os.getuid()
        proc = await asyncio.create_subprocess_exec(
            "launchctl", "kickstart", "-k", f"gui/{uid}/{service}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


async def main_loop():
    global _last_bot_status, _last_orch_status, _alerted

    logger.info("Watchdog starting")

    while True:
        try:
            bot_alive = await check_bot_alive()
            orch_alive = await check_orchestrator_alive()

            # Bot went down
            if _last_bot_status and not bot_alive:
                logger.warning("Bot is DOWN")
                await set_bot_description(False)
                await alert_admin(
                    "\u26a0\ufe0f CTO Bot is down!\n\n"
                    "Clients will see 'temporarily offline' message.\n"
                    "Attempting auto-restart..."
                )
                restarted = await try_restart_service("dev.wingmen.ctobot")
                if restarted:
                    await asyncio.sleep(5)
                    if await check_bot_alive():
                        logger.info("Bot auto-restarted successfully")
                        await alert_admin("\u2705 Bot auto-restarted successfully")
                        await set_bot_description(True)
                        bot_alive = True

            # Bot came back up
            if not _last_bot_status and bot_alive:
                logger.info("Bot is back UP")
                await set_bot_description(True)
                if _alerted:
                    await alert_admin("\u2705 CTO Bot is back online")
                    _alerted = False

            # Orchestrator went down
            if _last_orch_status and not orch_alive:
                logger.warning("Orchestrator is DOWN")
                await alert_admin(
                    "\u26a0\ufe0f Orchestrator is down!\n\n"
                    "Build jobs won't process until it's back.\n"
                    "Attempting auto-restart..."
                )
                restarted = await try_restart_service("dev.wingmen.orchestrator")
                if restarted:
                    await asyncio.sleep(5)
                    if await check_orchestrator_alive():
                        logger.info("Orchestrator auto-restarted successfully")
                        await alert_admin("\u2705 Orchestrator auto-restarted successfully")
                        orch_alive = True

            if not _last_orch_status and orch_alive:
                logger.info("Orchestrator is back UP")
                await alert_admin("\u2705 Orchestrator is back online")

            _last_bot_status = bot_alive
            _last_orch_status = orch_alive
            if not bot_alive or not orch_alive:
                _alerted = True

        except Exception as e:
            logger.error(f"Watchdog error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
