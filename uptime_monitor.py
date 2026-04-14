"""Uptime monitor — periodic HTTP pings with Telegram alerting.

Checks configured targets every ~5 minutes, alerts on failure,
deduplicates alerts, and clears dedup on recovery.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("wingmen.uptime_monitor")

TARGETS = [
    {"name": "ihsanOS", "url": "https://ihsanos.com"},
    {"name": "cosem-tdu", "url": "https://tdu-tools-prod.web.app"},
]


async def check_target(client: httpx.AsyncClient, url: str) -> dict:
    """HTTP GET a target and return status dict."""
    start = time.monotonic()
    try:
        resp = await client.get(url, timeout=10, follow_redirects=True)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "up": resp.status_code < 500,
            "status_code": resp.status_code,
            "error": None,
            "response_time_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "up": False,
            "status_code": None,
            "error": str(exc),
            "response_time_ms": elapsed_ms,
        }


async def poll_uptime(supabase, bot=None, musa_chat_id=None) -> None:
    """Check all targets, alert on failure, log to bot_heartbeat."""
    results = []

    async with httpx.AsyncClient(timeout=15) as client:
        for target in TARGETS:
            result = await check_target(client, target["url"])
            result["name"] = target["name"]
            results.append(result)

    for r in results:
        dedup_key = f"uptime:{r['name']}:down"

        if not r["up"]:
            try:
                existing = await supabase.table("notification_log").select("id").eq(
                    "dedup_key", dedup_key
                ).limit(1).execute()

                if not existing.data and bot and musa_chat_id:
                    status_info = f"HTTP {r['status_code']}" if r["status_code"] else r["error"]
                    msg = f"🔴 {r['name']} is DOWN — {status_info}"
                    await bot.send_message(chat_id=musa_chat_id, text=msg)

                    await supabase.table("notification_log").insert({
                        "source": "uptime_monitor",
                        "channel": "telegram",
                        "recipient": str(musa_chat_id),
                        "message_text": msg,
                        "dedup_key": dedup_key,
                    }).execute()
            except Exception as e:
                logger.warning(f"Alert handling failed for {r['name']}: {e}")
        else:
            try:
                await supabase.table("notification_log").delete().eq(
                    "dedup_key", dedup_key
                ).execute()
            except Exception as e:
                logger.warning(f"Dedup cleanup failed for {r['name']}: {e}")

    all_up = all(r["up"] for r in results)
    try:
        await supabase.table("bot_heartbeat").upsert({
            "service": "uptime_monitor",
            "status": "healthy" if all_up else "degraded",
            "last_ping": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "targets": [
                    {
                        "name": r["name"],
                        "up": r["up"],
                        "status_code": r["status_code"],
                        "response_time_ms": r["response_time_ms"],
                    }
                    for r in results
                ],
            },
        }, on_conflict="service").execute()
    except Exception as e:
        logger.warning(f"Heartbeat write failed: {e}")
