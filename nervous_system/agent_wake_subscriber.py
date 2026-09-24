#!/usr/bin/env python3
"""agent_wake_subscriber.py — the Mini-side realtime AUTO-WAKE subscriber (Gap B).

Runs the existing CAI-259 #111 auto-wake doorbell as a standalone, WAKE-ONLY
daemon on the Mac Mini, so a directed bus row (`agent_messages` INSERT) wakes the
recipient Mini lane the INSTANT it lands — ending the manual-nudge stalls
(cc-irsyad stalled ~5x/day on unread hub tasks).

WHY A SEPARATE DAEMON (Gap B, per hub + CAI-706):
  * `agent_wake.wake_agent` does a LOCAL `tmux send-keys`, so the subscriber MUST
    run on the same host as the lanes it wakes. This wakes MINI lanes only.
  * `subscribe_agent_messages` IS wired into wingmen_orch.py (full mode: Telegram
    push + wake) — but wingmen_orch.py is not currently running, so the wake path
    is dormant. This daemon activates ONLY the wake, independent of that.

WAKE-ONLY (no Telegram side effects): we pass wake_only=True, so each INSERT runs
the #111 doorbell and then returns BEFORE the Telegram-forward pipeline. That
avoids marking rows forwarded-without-sending (which would starve the 5-min poll).
The wake itself is a fixed content-free signal (CAI-255 #2), kill-switch gated
(AUTO_WAKE_ENABLED), debounced (45s) and hard-capped (5/5min, fails loud) inside
agent_wake — this daemon adds no policy.

CAI-706 honesty: ONE subscriber, on ONE host (the Mini). It wakes Mini lanes
only; it does NOT cover VPS/Studio lanes (they'd each need their own local
subscriber). The kill-switch is AUTO_WAKE_ENABLED=0 in .env.

Run: python3 -m nervous_system.agent_wake_subscriber
Launchd: dev.wingmen.agent-wake-subscriber (KeepAlive), boot via
scripts/boot_agent_wake_subscriber.sh.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from supabase import acreate_client

from nervous_system import agent_wake
from nervous_system.agent_messages_realtime import _LivenessTracker, subscribe_agent_messages

_ORCH = Path(__file__).resolve().parent.parent
load_dotenv(_ORCH / ".env")

HEARTBEAT_FILE = _ORCH / "logs" / "agent_wake_subscriber_heartbeat"
LOG_FILE = _ORCH / "logs" / "agent-wake-subscriber.log"
_HEARTBEAT_SEC = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
logger = logging.getLogger("wingmen.agent_wake_subscriber")


def _heartbeat_payload(tracker: _LivenessTracker, now_epoch: float) -> str:
    """The heartbeat body: a wall-clock ts (freshness, as before) PLUS the 5B
    delivery-lag state, so an external CAI-771 dead-monitor can compare the exposed
    last_realtime_id against DB max(id) and ALERT LOUD if the in-process exit(1) ever
    fails to fire. JSON so fields are unambiguous; `ts` stays the freshness signal."""
    return json.dumps({
        "ts": now_epoch,
        "last_realtime_id": tracker.last_realtime_id,
        "db_max": tracker.last_db_max,
        "lag": tracker.lag(),
    })


async def _heartbeat_loop(tracker: _LivenessTracker) -> None:
    """Write a heartbeat every _HEARTBEAT_SEC so a watchdog can spot a wedged/dead
    subscriber (parity with weekly_limit_monitor / weekly_alert_relay). Now also
    exposes the delivery-lag state (5B) for the external dead-monitor belt."""
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            HEARTBEAT_FILE.write_text(_heartbeat_payload(tracker, time.time()))
        except Exception as e:  # noqa: BLE001
            logger.warning("heartbeat write failed: %s", e)
        await asyncio.sleep(_HEARTBEAT_SEC)


async def main() -> None:
    if not agent_wake.auto_wake_enabled():
        # Not fatal — the subscriber still runs (so a later flip needs no restart
        # of THIS daemon; agent_wake.auto_wake_enabled() is re-checked per row),
        # but be loud that the kill-switch is off so a "why no wakes" is obvious.
        logger.warning(
            "AUTO_WAKE_ENABLED is OFF — subscriber will run but wakes are "
            "suppressed by the kill-switch until it is set to 1.")
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    supabase = await acreate_client(url, key)
    logger.info(
        "agent-wake-subscriber up — WAKE-ONLY, Mini host, AUTO_WAKE_ENABLED=%s",
        agent_wake.auto_wake_enabled())
    # subscribe_agent_messages owns its own reconnect/resubscribe loop; we run it
    # alongside the heartbeat. If it ever returns/raises to here, log + let launchd
    # KeepAlive restart the process. The shared _LivenessTracker (5B) is written by
    # the subscription's delivery callback and read by the heartbeat loop, so a silent
    # stall is BOTH self-healed (exit 1 → KeepAlive resubscribe) AND exposed on the
    # heartbeat for the external belt.
    tracker = _LivenessTracker()
    await asyncio.gather(
        subscribe_agent_messages(supabase, bot=None, musa_chat_id=None,
                                 wake_only=True, liveness=tracker),
        _heartbeat_loop(tracker),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
