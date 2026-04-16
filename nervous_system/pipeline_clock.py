"""
pipeline_clock.py — Daily days_clean increment for bug_pipeline_readiness.

TASK-042: Fix days_clean counter not incrementing.

Runs once per 24 hours (tracked via wingmen_config.pipeline_clock_last_run).
On each tick:

  1. Check if any gate status flipped to 'breach' since last tick.
     If so: reset days_clean=0 on all green gates + Telegram alert.

  2. If no breach: increment days_clean by 1 on all green gates.

  3. If ALL gates reach days_clean >= required_days_clean:
     Telegram Musa "Phase 0 complete — pipeline ready for TDU pilot".

The orchestrator calls tick_pipeline_clock() every 10 polls (~5 min).
The function checks the config key to determine whether 24h has elapsed
before doing any DB work — so it's safe to call frequently.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from nervous_system import error_tracker

logger = logging.getLogger("wingmen.pipeline_clock")

_CONFIG_KEY = "pipeline_clock_last_run"
_TICK_INTERVAL_HOURS = 24


async def tick_pipeline_clock(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Advance the pipeline clock if 24h have elapsed since the last tick.

    Safe to call every few minutes — does nothing until the interval elapses.
    """
    try:
        now = datetime.now(timezone.utc)
        last_run = await _get_last_run(supabase)

        if last_run is not None:
            elapsed = now - last_run
            if elapsed < timedelta(hours=_TICK_INTERVAL_HOURS):
                return  # Not yet time

        logger.info("pipeline_clock: 24h elapsed — running tick")
        await _run_tick(supabase, now, bot, musa_chat_id)
        await _set_last_run(supabase, now)

    except Exception as e:
        logger.error(f"pipeline_clock tick failed: {e}")
        error_tracker.track_exception("pipeline_clock.tick", e)


async def _run_tick(
    supabase,
    now: datetime,
    bot=None,
    musa_chat_id: str | None = None,
) -> None:
    """Core tick logic: check for breach, increment or reset, check completion."""
    try:
        result = await supabase.table("bug_pipeline_readiness").select("*").execute()
        gates = result.data or []
    except Exception as e:
        logger.error(f"Failed to load bug_pipeline_readiness: {e}")
        error_tracker.track_exception("pipeline_clock.load_gates", e)
        return

    if not gates:
        logger.warning("pipeline_clock: no gates found in bug_pipeline_readiness")
        return

    green_gates = [g for g in gates if g["status"] == "green"]
    breach_gates = [g for g in gates if g["status"] == "breach"]

    if breach_gates:
        await _handle_breach(supabase, now, breach_gates, green_gates, bot, musa_chat_id)
    else:
        await _increment_clean(supabase, now, green_gates, bot, musa_chat_id)


async def _handle_breach(
    supabase,
    now: datetime,
    breach_gates: list[dict],
    green_gates: list[dict],
    bot=None,
    musa_chat_id: str | None = None,
) -> None:
    """Reset days_clean to 0 for all green gates and alert Musa."""
    refs = [g["gate_ref"] for g in breach_gates]
    logger.warning(f"pipeline_clock: breach detected in {refs} — resetting days_clean")

    # Reset all green gates
    try:
        await supabase.table("bug_pipeline_readiness").update(
            {
                "days_clean": 0,
                "last_breach_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        ).eq("status", "green").execute()
    except Exception as e:
        logger.error(f"Failed to reset days_clean: {e}")
        error_tracker.track_exception("pipeline_clock.reset_breach", e)

    if bot and musa_chat_id:
        breach_list = "\n".join(f"  • {g['gate_ref']}: {g['gate_title']}" for g in breach_gates)
        msg = (
            f"\u26a0\ufe0f Pipeline breach detected\n\n"
            f"Gates in breach:\n{breach_list}\n\n"
            f"days_clean reset to 0 for all green gates."
        )
        try:
            await bot.send_message(chat_id=musa_chat_id, text=msg)
            logger.info("pipeline_clock: breach alert sent to Telegram")
        except Exception as e:
            logger.error(f"Failed to send breach Telegram: {e}")
            error_tracker.track_exception("pipeline_clock.breach_telegram", e)


async def _increment_clean(
    supabase,
    now: datetime,
    green_gates: list[dict],
    bot=None,
    musa_chat_id: str | None = None,
) -> None:
    """Increment days_clean by 1 for all green gates; check completion."""
    if not green_gates:
        logger.debug("pipeline_clock: no green gates to increment")
        return

    # Increment each green gate individually to handle the new value
    all_complete = True
    for gate in green_gates:
        new_days = gate["days_clean"] + 1
        required = gate["required_days_clean"]
        try:
            await supabase.table("bug_pipeline_readiness").update(
                {"days_clean": new_days, "updated_at": now.isoformat()}
            ).eq("id", gate["id"]).execute()
        except Exception as e:
            logger.error(
                f"Failed to increment days_clean for {gate['gate_ref']}: {e}"
            )
            error_tracker.track_exception("pipeline_clock.increment", e)
            all_complete = False
            continue

        logger.info(
            f"  {gate['gate_ref']}: days_clean={new_days}/{required}"
        )
        if new_days < required:
            all_complete = False

    if all_complete:
        await _notify_phase_complete(supabase, now, bot, musa_chat_id)


async def _notify_phase_complete(
    supabase,
    now: datetime,
    bot=None,
    musa_chat_id: str | None = None,
) -> None:
    """Notify Musa that Phase 0 is complete — all gates are clean for required days."""
    dedup_key = "pipeline_clock:phase_0_complete"
    try:
        existing = (
            await supabase.table("notification_log")
            .select("id")
            .eq("dedup_key", dedup_key)
            .limit(1)
            .execute()
        )
        if existing.data:
            logger.info("pipeline_clock: phase_0 completion already notified")
            return
    except Exception as e:
        logger.warning(f"Dedup check for phase_0 complete failed: {e}")

    msg = (
        "\u2705 Phase 0 complete\n\n"
        "All bug_pipeline_readiness gates have held clean for their required days.\n"
        "Pipeline is ready for TDU pilot."
    )

    if bot and musa_chat_id:
        try:
            sent = await bot.send_message(chat_id=musa_chat_id, text=msg)
            logger.info("pipeline_clock: Phase 0 complete notification sent")
        except Exception as e:
            logger.error(f"Failed to send Phase 0 complete Telegram: {e}")
            error_tracker.track_exception("pipeline_clock.phase_complete_telegram", e)
            return  # Don't log dedup entry if send failed

    try:
        await supabase.table("notification_log").insert(
            {
                "source": "pipeline_clock",
                "decision_ref": "phase_0_complete",
                "channel": "telegram",
                "recipient": musa_chat_id or "unknown",
                "message_text": msg,
                "dedup_key": dedup_key,
            }
        ).execute()
    except Exception as e:
        logger.error(f"Failed to log Phase 0 complete notification: {e}")
        error_tracker.track_exception("pipeline_clock.log_phase_complete", e)


async def _get_last_run(supabase) -> datetime | None:
    """Read pipeline_clock_last_run from wingmen_config."""
    try:
        result = (
            await supabase.table("wingmen_config")
            .select("value")
            .eq("key", _CONFIG_KEY)
            .limit(1)
            .execute()
        )
        if result.data:
            return datetime.fromisoformat(result.data[0]["value"])
        return None
    except Exception as e:
        logger.warning(f"Failed to read pipeline_clock_last_run: {e}")
        error_tracker.track_exception("pipeline_clock.get_last_run", e)
        return None


async def _set_last_run(supabase, now: datetime) -> None:
    """Upsert pipeline_clock_last_run in wingmen_config."""
    try:
        await supabase.table("wingmen_config").upsert(
            {
                "key": _CONFIG_KEY,
                "value": now.isoformat(),
                "description": "Last time pipeline_clock ran a daily tick",
                "updated_by": "pipeline_clock",
                "updated_at": now.isoformat(),
            }
        ).execute()
    except Exception as e:
        logger.error(f"Failed to write pipeline_clock_last_run: {e}")
        error_tracker.track_exception("pipeline_clock.set_last_run", e)
