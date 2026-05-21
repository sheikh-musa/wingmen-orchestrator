"""Wingmen Watchdog — monitors bot + orchestrator health.

Runs as a separate LaunchAgent. Checks every 60s if services are alive.
Alerts Musa via Telegram if anything is down.
Sets bot description to "offline" so clients see status before messaging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import psycopg
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
from nervous_system.long_caller_watchdog import (
    decide_kill_with_pid_verify,
    build_telegram_body,
)

# CAI-RESP-163 Phase B — long_caller_watchdog poll constants
_LONG_CALLER_POLL_INTERVAL = 300  # 5min per CAI-RESP-163 Q2
_long_caller_last_poll = 0.0

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


# ── CAI-WATCHDOG-MAC-STUDIO-001 — Mac Studio endpoint probes ──────────────
# Tailscale-bound services on Mac Studio; nohup-spawned, don't survive
# reboot. No remote restart capability — alerts only.

MAC_STUDIO_HOST = "100.104.36.27"
MAC_STUDIO_ENCODER_URL = f"http://{MAC_STUDIO_HOST}:8080/health"
MAC_STUDIO_MLX_URL = f"http://{MAC_STUDIO_HOST}:8081/health"
MAC_STUDIO_ALERT_AFTER_SECONDS = 300  # 5 minutes per spec


class MacStudioEndpointState:
    """Per-endpoint hysteresis tracker. Alert only after sustained failure
    for alert_after_seconds; alert once per outage; signal recovery once."""

    def __init__(self, name: str, alert_after_seconds: int = 300):
        self.name = name
        self.alert_after_seconds = alert_after_seconds
        self.first_failure_at: float | None = None
        self.alerted: bool = False

    def record_probe(self, alive: bool, now_epoch: float) -> bool:
        """Record a probe result. Returns True iff this probe caused a
        transition event (alert-fires or recovery-fires), False otherwise."""
        if alive:
            if self.alerted:
                # recovery transition
                self.first_failure_at = None
                self.alerted = False
                return True
            # alive and never alerted — no event
            self.first_failure_at = None
            return False
        # not alive
        if self.first_failure_at is None:
            self.first_failure_at = now_epoch
            return False
        if self.alerted:
            # still down, already alerted
            return False
        # threshold check
        if now_epoch - self.first_failure_at >= self.alert_after_seconds:
            self.alerted = True
            return True
        return False


async def check_mac_studio_encoder() -> bool:
    """Probe the bge-m3 encoder on Mac Studio. True if /health returns 200."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(MAC_STUDIO_ENCODER_URL)
            return resp.status_code == 200
    except Exception:
        return False


async def check_mac_studio_mlx() -> bool:
    """Probe mlx_lm.server on Mac Studio. True if /health returns 200."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(MAC_STUDIO_MLX_URL)
            return resp.status_code == 200
    except Exception:
        return False


_encoder_state = MacStudioEndpointState(
    name="bge-m3-encoder", alert_after_seconds=MAC_STUDIO_ALERT_AFTER_SECONDS
)
_mlx_state = MacStudioEndpointState(
    name="mlx_lm-server", alert_after_seconds=MAC_STUDIO_ALERT_AFTER_SECONDS
)


async def _probe_mac_studio() -> None:
    """One probe cycle for both Mac Studio endpoints. Alerts on transitions."""
    import time as _time
    now = _time.time()

    for state, probe, port, role in [
        (_encoder_state, check_mac_studio_encoder, 8080, "bge-m3 encoder"),
        (_mlx_state, check_mac_studio_mlx, 8081, "mlx_lm.server"),
    ]:
        alive = await probe()
        transitioned = state.record_probe(alive=alive, now_epoch=now)
        if transitioned:
            if state.alerted:
                logger.warning(
                    f"Mac Studio {state.name} ({MAC_STUDIO_HOST}:{port}) UNREACHABLE "
                    f"for >{state.alert_after_seconds}s"
                )
                await alert_admin(
                    f"⚠️ Mac Studio {role} ({MAC_STUDIO_HOST}:{port}) "
                    f"unreachable for >5min.\n\n"
                    f"Impact: mizan_bot fiqh retrieval falls back to FTS-only "
                    f"(silent degradation — semantic-first stops contributing).\n\n"
                    f"Action: Screen Share into Mac Studio + relaunch via "
                    f"nohup. No auto-restart possible from Mac Mini."
                )
            else:
                logger.info(f"Mac Studio {state.name} is back UP")
                await alert_admin(
                    f"✅ Mac Studio {role} ({MAC_STUDIO_HOST}:{port}) "
                    f"is back online."
                )


async def _long_caller_sweep() -> None:
    """One sweep cycle per CAI-RESP-163: read active_autonomous_loops +
    long_running_claude_callers, decide kills, actuate SIGTERM, alert admin,
    write notification_log audit. Pure-Python decision via long_caller_watchdog;
    this function does the I/O.
    """
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        return

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cc_identity, sessions_24h, cadence_seconds, parent_pid "
                "FROM active_autonomous_loops"
            )
            flagged = cur.fetchall()
            cur.execute(
                "SELECT caller_name, auto_kill_policy FROM long_running_claude_callers "
                "WHERE revoked_at IS NULL"
            )
            registry = {r[0]: r[1] for r in cur.fetchall()}

    for cc_id, sessions_24h, cadence_seconds, parent_pid in flagged:
        # CC families register under -interactive suffix per CC-FAMILY-INTERACTIVE-SESSIONS-001
        possible_names = [cc_id, f"{cc_id}-interactive"]
        registered = False
        policy = None
        matched_name = cc_id
        for name in possible_names:
            if name in registry:
                registered = True
                policy = registry[name]
                matched_name = name
                break

        decision = decide_kill_with_pid_verify(
            caller_name=matched_name,
            sessions_24h=sessions_24h,
            cadence_seconds=cadence_seconds or 0,
            registered=registered,
            registered_policy=policy,
            parent_pid=parent_pid,
        )

        if decision.action == "hard_kill" and parent_pid:
            try:
                os.kill(parent_pid, signal.SIGTERM)
                logger.warning(
                    f"long_caller_watchdog: SIGTERM sent to PID {parent_pid} "
                    f"(caller {matched_name})"
                )
                body = build_telegram_body(
                    event="hard_kill",
                    caller_name=matched_name,
                    pid=parent_pid,
                    cwd="unknown",
                    sessions_24h=sessions_24h,
                    threshold=50,
                )
                await alert_admin(body)
                with psycopg.connect(dsn, autocommit=True) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO notification_log "
                            "(source, decision_ref, channel, recipient, message_text) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (
                                "watchdog_hard_kill",
                                "CAI-RESP-163",
                                "long_running_callers",
                                matched_name,
                                json.dumps({
                                    "sessions_24h": sessions_24h,
                                    "cadence_seconds": cadence_seconds,
                                    "pid": parent_pid,
                                    "reason": decision.reason,
                                }),
                            ),
                        )
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"SIGTERM to {parent_pid} failed: {e}")
        elif decision.action == "no_kill" and "pid_recycled" in decision.reason:
            # Q3 PID-recycle abort — audit-log the aborted kill
            with psycopg.connect(dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO notification_log "
                        "(source, decision_ref, channel, recipient, message_text) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            "watchdog_aborted_kill",
                            "CAI-RESP-163",
                            "long_running_callers",
                            matched_name,
                            json.dumps({"reason": decision.reason, "pid": parent_pid}),
                        ),
                    )


async def _maybe_long_caller_poll() -> None:
    """Gated wrapper — only fires every _LONG_CALLER_POLL_INTERVAL seconds."""
    global _long_caller_last_poll
    now = time.time()
    if now - _long_caller_last_poll < _LONG_CALLER_POLL_INTERVAL:
        return
    _long_caller_last_poll = now
    try:
        await _long_caller_sweep()
    except Exception as e:
        logger.error(f"_long_caller_sweep failed: {e}")


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

            await _probe_mac_studio()

            await _maybe_long_caller_poll()

            _last_bot_status = bot_alive
            _last_orch_status = orch_alive
            if not bot_alive or not orch_alive:
                _alerted = True

        except Exception as e:
            logger.error(f"Watchdog error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
