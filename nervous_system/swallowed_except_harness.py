"""Swallowed Exception Harness — tracks silent except blocks and escalates repeaters.

In-memory counter tracks every except block that fires. When the same label
fires repeatedly within a time window, escalates to CTO via Telegram.
Deduplicates via notification_log, with recovery sweep to allow re-alerting.
"""

from __future__ import annotations

import logging
import time

from supabase import AsyncClient as SupabaseAsyncClient
from telegram import Bot

from notification_router import get_chat_id

logger = logging.getLogger("wingmen.swallowed_except_harness")


class SwallowedExceptCounter:
    """In-memory counter mapping label -> list of monotonic timestamps."""

    def __init__(self, window_seconds: int = 600):
        self._counts: dict[str, list[float]] = {}
        self.window_seconds = window_seconds

    def record(self, label: str) -> None:
        now = time.monotonic()
        if label not in self._counts:
            self._counts[label] = []
        self._counts[label].append(now)
        self._prune(label, now)

    def count(self, label: str) -> int:
        now = time.monotonic()
        self._prune(label, now)
        return len(self._counts.get(label, []))

    def _prune(self, label: str, now: float) -> None:
        if label not in self._counts:
            return
        cutoff = now - self.window_seconds
        self._counts[label] = [t for t in self._counts[label] if t > cutoff]
        if not self._counts[label]:
            del self._counts[label]


# Prune window MUST exceed check_interval x threshold, or slow-cadence tasks are
# structurally un-escalatable. The escalation check runs ~every 30 min
# (wingmen_orch swallowed_except_counter >= 60, ~30s/poll = 1800s); at the default
# threshold=3 the floor is 1800*3 = 5400s. Below that floor, a task firing at most
# once per window (e.g. the ~30-min in-loop feature_health / drift_audit /
# cc_session_costs tasks) has each failure pruned before the next fires, so the
# count never exceeds 1 and it never alerts (the feature_health blind spot).
# 7200s (2h) clears the floor with margin.
SWALLOWED_PRUNE_WINDOW_S = 7200

counter = SwallowedExceptCounter(window_seconds=SWALLOWED_PRUNE_WINDOW_S)


def _alert_text(label: str, cnt: int, window_seconds: int) -> str:
    """Build the escalation alert. The window is DERIVED from window_seconds so the
    text can never lie about its own window (the defect that hid in the dead param)."""
    hours = max(1, window_seconds // 3600)
    return (
        f"\U0001f507 Silent exception repeating: {label}\n\n"
        f"Hit {cnt}x in the last {hours}h\n"
        f"This except block is swallowing errors silently.\n\n"
        f"Check logs or grep wingmen_orch.py for this label."
    )


def record_swallowed(label: str, exc: Exception) -> None:
    """One-liner for callsites — debug-logs and records the exception."""
    logger.debug(f"Swallowed exception [{label}]: {exc}")
    counter.record(label)


async def check_swallowed_escalation(
    supabase: SupabaseAsyncClient,
    bot: Bot,
    threshold: int = 3,
    window_seconds: int = SWALLOWED_PRUNE_WINDOW_S,
) -> None:
    """Check if any swallowed-exception label has fired repeatedly and escalate."""

    # Make window_seconds actually drive pruning. Previously this param was dead:
    # _prune/count read the counter's own field, so the visible knob did nothing.
    counter.window_seconds = window_seconds

    cto_id = get_chat_id("cto")
    if not cto_id:
        return

    now = time.monotonic()

    # Check each label for threshold breach
    for label in list(counter._counts.keys()):
        counter._prune(label, now)
        cnt = counter.count(label)
        if cnt < threshold:
            continue

        dedup_key = f"swallowed_except:{label}"

        try:
            existing = await supabase.table("notification_log").select("id").eq(
                "dedup_key", dedup_key
            ).limit(1).execute()
            if existing.data:
                continue
        except Exception as e:
            logger.warning(f"Dedup check failed for {dedup_key}: {e}")

        try:
            await bot.send_message(
                chat_id=cto_id,
                text=_alert_text(label, cnt, counter.window_seconds),
            )
        except Exception as e:
            logger.error(f"Swallowed-except alert for {label} failed: {e}")
            continue

        try:
            await supabase.table("notification_log").insert({
                "source": "swallowed_except_harness",
                "channel": "telegram",
                "recipient": cto_id,
                "message_text": f"Swallowed exception repeating: {label} ({cnt}x)",
                "dedup_key": dedup_key,
            }).execute()
        except Exception as e:
            logger.error(f"Failed to log notification for {label}: {e}")

    # Recovery sweep: clear dedup keys when counts drop below threshold // 2
    try:
        dedup_rows = await supabase.table("notification_log").select("id, dedup_key").eq(
            "source", "swallowed_except_harness"
        ).execute()

        for row in (dedup_rows.data or []):
            key = row["dedup_key"]
            if not key.startswith("swallowed_except:"):
                continue
            label = key.split(":", 1)[1]
            cnt = counter.count(label)
            if cnt < threshold // 2:
                await supabase.table("notification_log").delete().eq(
                    "id", row["id"]
                ).execute()
    except Exception as e:
        logger.warning(f"Recovery sweep failed: {e}")
