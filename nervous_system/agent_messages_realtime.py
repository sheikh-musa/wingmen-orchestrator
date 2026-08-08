"""CADENCE-003 Strategy A Level 1: sub-second push of agent_messages to Telegram.

Replaces the 30s polling latency with a Supabase Realtime WebSocket subscription
on INSERT events. The existing poll cadence (5min in main_loop) remains as
belt-and-suspenders — on WS disconnect or missed event, the poll closes the gap.

Design:
  - Long-running coroutine subscribe_agent_messages() opens a Realtime channel
    on the `agent_messages` table, INSERT-only.
  - Each INSERT delivers payload.new (the inserted row). We re-fetch full row
    state from Postgres because Realtime's payload may not include every column
    after RLS/trigger transformations.
  - Per-message routing reuses the existing helpers in agent_messages_poll —
    same dedup (notification_log), same _format_telegram, same
    _mark_forwarded semantics. No duplicate logic.
  - AsyncRealtimeClient.auto_reconnect handles transient disconnects; the
    coroutine logs and continues. Hard failures bubble up; orch's
    record_swallowed catches them.
  - No autonomous decisions per CADENCE-003 — this is purely a transport
    swap. The existing routability rules (CC-to-CC suppression, P3 skip,
    requires_response audit-visibility) carry through unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from nervous_system import agent_messages_poll
from nervous_system import agent_wake
from nervous_system.error_tracker import track_exception

logger = logging.getLogger("wingmen.agent_messages_realtime")

_CHANNEL_NAME = "agent_messages_insert_v1"
_TABLE = "agent_messages"
_RECONNECT_BACKOFF_SECONDS = 30  # if subscribe() itself raises, wait this long

# 5B silent-stall self-heal (op#11322 anti-wedge arc). The stall that stranded the
# Mini subscriber ~7.5h and the hub ~22h was is_connected==True while the server had
# SILENTLY stopped pushing INSERTs — so is_connected is a liar and cannot be the sole
# liveness gate. We instead compare the last insert id actually DELIVERED via the
# callback against DB max(id): a gap that PERSISTS past this grace window is a
# confirmed stall → the subscriber exits(1) and KeepAlive/Restart resubscribes fresh.
_STALL_GRACE_SECONDS = 120   # a gap must persist this long before we call it a stall
_LAG_POLL_SECONDS = 30       # how often the inner loop checks delivery lag


class _LivenessTracker:
    """Pure delivery-lag decision logic (no I/O), so it is unit-testable.

    `note_delivered` advances on every INSERT the realtime callback actually
    delivers; `seed` baselines to DB max(id) at subscribe time (so a fresh boot,
    where nothing has been delivered yet, does not read as a huge lag); `evaluate`
    returns True only once a gap between DB max(id) and the last delivered id has
    persisted for `grace_seconds` — an idle bus (db_max frozen) never stalls, and
    normal delivery latency inside the grace window is absorbed.
    """

    def __init__(self, grace_seconds: float = _STALL_GRACE_SECONDS) -> None:
        self.grace = grace_seconds
        self.last_realtime_id = 0
        self.last_db_max = 0
        self._lag_since: float | None = None  # monotonic ts a still-open gap first appeared

    def note_delivered(self, msg_id: int) -> None:
        if isinstance(msg_id, int) and msg_id > self.last_realtime_id:
            self.last_realtime_id = msg_id

    def seed(self, db_max: int) -> None:
        """Adopt db_max as caught-up (subscribe-time baseline); never move backward."""
        if isinstance(db_max, int) and db_max > self.last_realtime_id:
            self.last_realtime_id = db_max
        self.last_db_max = max(self.last_db_max, db_max or 0)
        self._lag_since = None

    def evaluate(self, db_max: int, now: float) -> bool:
        """Return True iff a delivery gap has persisted past the grace window."""
        self.last_db_max = db_max
        if self.last_realtime_id >= db_max:
            self._lag_since = None
            return False
        # a gap exists (DB is ahead of what realtime has delivered)
        if self._lag_since is None:
            self._lag_since = now
            return False
        return (now - self._lag_since) >= self.grace

    def lag(self) -> int:
        return max(0, self.last_db_max - self.last_realtime_id)


async def _db_max_id(supabase) -> int:
    """Current max(id) in agent_messages — the newest row the realtime feed OUGHT to
    have delivered. PostgREST has no aggregate, so order-desc-limit-1 is the idiom."""
    res = await (
        supabase.table(_TABLE)
        .select("id")
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return int(rows[0]["id"]) if rows else 0


def _stall_detected(lag: int) -> None:
    """A silent stall is confirmed: the WS reads connected but the DB has been ahead of
    the last delivered insert past the grace window. Fail LOUD, then exit(1) so the
    KeepAlive/Restart supervisor resubscribes with a FRESH client (the wedged client's
    is_connected can't be trusted). os._exit bypasses cleanup that could hang on the
    wedged socket; we flush logs first so the reason is on the record (charter #1)."""
    logger.error(
        "realtime: SILENT STALL DETECTED — DB is %d insert(s) ahead of the last "
        "delivered row while is_connected==True; exiting(1) for a fresh resubscribe (5B).",
        lag,
    )
    for h in list(logging.getLogger().handlers) + list(logger.handlers):
        try:
            h.flush()
        except Exception:  # noqa: BLE001
            pass
    os._exit(1)


async def _route_single_message(
    supabase, bot, musa_chat_id: str | None, msg_id: int, wake_only: bool = False
) -> None:
    """Fetch full row by id and run it through the same pipeline as poll.

    Reuses agent_messages_poll._is_routable / _format_telegram / _already_notified /
    _log_notification / _mark_forwarded / _mark_skipped so the realtime and poll
    paths behave identically for the same input row.
    """
    try:
        row_result = await (
            supabase.table(_TABLE)
            .select(
                "id, from_agent, to_agent, message_type, subject, body, "
                "requires_response, priority, created_at, is_test, "
                "read_at, forwarded_to_telegram_at, skipped_at"
            )
            .eq("id", msg_id)
            .limit(1)
            .execute()
        )
        rows = row_result.data or []
        if not rows:
            logger.debug(f"realtime: msg #{msg_id} not found on re-fetch (deleted?)")
            return
        msg = rows[0]
    except Exception as e:
        logger.error(f"realtime: re-fetch failed for msg #{msg_id}: {e}")
        track_exception("agent_messages_realtime.refetch", e)
        return

    # Skip if already-routed-by-poll (the 5-min belt-and-suspenders catches us).
    if msg.get("forwarded_to_telegram_at") or msg.get("skipped_at"):
        logger.debug(f"realtime: msg #{msg_id} already processed; skipping")
        return
    if msg.get("is_test"):
        return

    # #111 auto-wake (CAI-RESP-259) — INDEPENDENT of Telegram routing, and placed
    # BEFORE _is_routable because that gate drops CC-to-CC traffic, which is
    # exactly the inter-agent messages a recipient lane needs woken for. The wake
    # is a doorbell (fixed signal, zero authority); kill-switch gated; runs off the
    # event loop (resolve + send-keys are blocking). Cap-hit fails LOUD (Q4).
    if agent_wake.auto_wake_enabled() and agent_wake.should_auto_wake(
        msg.get("to_agent"), msg.get("message_type", ""),
        bool(msg.get("requires_response")), msg.get("priority", "P2"),
        bool(msg.get("is_test")),
    ):
        try:
            res = await asyncio.to_thread(agent_wake.wake_agent, msg["to_agent"], f"msg #{msg_id}")
            logger.info(f"realtime: auto-wake {msg.get('to_agent')} for #{msg_id}: {res}")
            if res.get("alert_due") and bot and musa_chat_id:
                await bot.send_message(
                    chat_id=musa_chat_id,
                    text=(f"⚠️ wake cap: {msg['to_agent']} hit {res.get('count')} wakes/5min "
                          f"— possible loop. Auto-wake paused for it; msg #{msg_id} still on the bus."),
                )
        except Exception as e:
            logger.error(f"realtime: auto-wake failed for #{msg_id}: {e}")
            track_exception("agent_messages_realtime.auto_wake", e)

    # WAKE-ONLY (Gap B): the standalone Mini subscriber (agent_wake_subscriber.py)
    # does ONLY the #111 doorbell and MUST NOT run the Telegram-forward pipeline
    # below — with bot=None that path still calls _mark_forwarded, which would mark
    # routable rows forwarded-without-sending and starve the 5-min belt-and-
    # suspenders poll. Additive: default False leaves the wingmen_orch full-mode
    # caller (Telegram push + wake) byte-for-byte unchanged.
    if wake_only:
        return

    if not agent_messages_poll._is_routable(msg):
        logger.debug(f"realtime: msg #{msg_id} not routable")
        return

    dedup_key = f"agent_message:{msg_id}:telegram"
    if await agent_messages_poll._already_notified(supabase, dedup_key, msg_id):
        return

    text = agent_messages_poll._format_telegram(msg)
    if text is None:
        # Same audit-visibility rule as poll: requires_response=true rows stay
        # unstamped so cai's CAI-PING-PROTOCOL-001 audit can still see them.
        if msg.get("requires_response"):
            return
        await agent_messages_poll._mark_skipped(supabase, msg_id)
        return

    sent_telegram_id: int | None = None
    if bot and musa_chat_id:
        try:
            sent = await bot.send_message(chat_id=musa_chat_id, text=text)
            sent_telegram_id = sent.message_id
            logger.info(
                f"realtime: msg #{msg_id} pushed to Telegram "
                f"(type={msg.get('message_type')}, requires_response={msg.get('requires_response')})"
            )
        except Exception as e:
            logger.error(f"realtime: Telegram send failed for msg #{msg_id}: {e}")
            track_exception("agent_messages_realtime.telegram_send", e)
            return  # don't mark forwarded if telegram failed

    await agent_messages_poll._log_notification(
        supabase,
        msg_id=msg_id,
        subject=msg.get("subject", "")[:100],
        text=text,
        musa_chat_id=musa_chat_id,
        telegram_msg_id=sent_telegram_id,
        dedup_key=dedup_key,
    )
    await agent_messages_poll._mark_forwarded(supabase, msg_id)


async def subscribe_agent_messages(
    supabase, bot=None, musa_chat_id: str | None = None, wake_only: bool = False,
    liveness: "_LivenessTracker | None" = None,
) -> None:
    """Long-running coroutine: subscribe to agent_messages INSERT events.

    Spawned as a background task from wingmen_orch's main entry; restart-safe
    because dedup keys + forwarded_to_telegram_at idempotently gate re-sends.

    Lifecycle:
      1. realtime.connect() establishes WS (auto_reconnect handles drops)
      2. channel.on_postgres_changes(INSERT, public.agent_messages) registers
         our callback
      3. channel.subscribe() joins the channel — server starts pushing INSERTs
      4. Callback dispatches per-message via _route_single_message
      5. If subscribe() raises, wait _RECONNECT_BACKOFF_SECONDS and retry

    Per CADENCE-003 INV-3 (MAX-first) and INV-5 (full audit): this module
    introduces zero autonomous decisions; it is a pure transport swap. The
    existing notification_log audit trail captures every routed message.
    """
    realtime = supabase.realtime
    # 5B: the delivery-lag tracker. Created once per process (persists across
    # reconnects; re-seeded after each subscribe). The caller may pass its own so an
    # external heartbeat can read the lag (subscriber daemon does this); wingmen_orch
    # passes none and gets an internal one.
    tracker = liveness if liveness is not None else _LivenessTracker()

    async def _handle_insert(payload: dict) -> None:
        try:
            data = payload.get("data", {})
            record = data.get("record") or {}
            msg_id = record.get("id")
            if not isinstance(msg_id, int):
                logger.debug(f"realtime: INSERT payload without int id: {payload}")
                return
            tracker.note_delivered(msg_id)  # 5B: proof the feed is live
            logger.info(f"realtime: _on_insert fired for msg #{msg_id}")
            await _route_single_message(supabase, bot, musa_chat_id, msg_id, wake_only)
        except Exception as e:
            logger.error(f"realtime: _on_insert handler failed: {e}")
            track_exception("agent_messages_realtime.callback", e)

    def _on_insert(payload: dict) -> None:
        # This realtime lib invokes the callback SYNCHRONOUSLY and does NOT await
        # a returned coroutine (RuntimeWarning: coroutine ... was never awaited).
        # A bare `async def` callback was therefore created-and-dropped — the whole
        # realtime path silently no-op'd, masking it behind the 5-min poll and
        # leaving #111 auto-wake inert. Schedule the async handler on the running
        # loop so it actually executes.
        asyncio.ensure_future(_handle_insert(payload))

    while True:
        try:
            if not realtime.is_connected:
                await realtime.connect()
                logger.info("realtime: WS connected")

            channel = supabase.channel(_CHANNEL_NAME)
            channel.on_postgres_changes(
                event="INSERT",
                schema="public",
                table=_TABLE,
                callback=_on_insert,
            )
            await channel.subscribe()
            logger.info(
                f"realtime: subscribed to {_TABLE} INSERT events on channel {_CHANNEL_NAME}"
            )
            # 5B: baseline the tracker to the current max at subscribe time — we only
            # own FUTURE inserts, so a fresh boot (nothing delivered yet) must not read
            # as a huge lag. Rows before subscribe are the poll's / backstop-sweep's job.
            try:
                tracker.seed(await _db_max_id(supabase))
            except Exception as e:  # noqa: BLE001
                logger.warning("realtime: could not seed lag baseline: %s", e)

            # supabase-py 2.28+: AsyncRealtimeClient.listen() is a deprecated
            # no-op; the WebSocket pump runs in the client's internal
            # _listen_task. After subscribe(), callbacks fire automatically.
            # We keep THIS coroutine alive and poll every _LAG_POLL_SECONDS — but
            # is_connected ALONE is NOT trusted (5B): the silent stall that stranded
            # the Mini ~7.5h and the hub ~22h was is_connected==True while the server
            # had stopped pushing INSERTs. So each tick also compares DB max(id) vs the
            # last delivered id; a gap persisting past the grace window is a confirmed
            # stall → exit(1) for a fresh resubscribe. A real disconnect still breaks to
            # the outer loop to reconnect+resubscribe.
            while realtime.is_connected:
                await asyncio.sleep(_LAG_POLL_SECONDS)
                try:
                    cur_max = await _db_max_id(supabase)
                except Exception as e:  # noqa: BLE001
                    logger.warning("realtime: lag-check db_max query failed: %s", e)
                    continue
                if tracker.evaluate(cur_max, time.monotonic()):
                    _stall_detected(tracker.lag())  # LOUD + os._exit(1); never returns
            logger.warning("realtime: WS dropped, will reconnect+resubscribe")
        except asyncio.CancelledError:
            logger.info("realtime: subscription cancelled, closing")
            try:
                await realtime.close()
            except Exception:
                pass
            raise
        except Exception as e:
            logger.error(
                f"realtime: subscribe loop failed ({e}); "
                f"retrying in {_RECONNECT_BACKOFF_SECONDS}s"
            )
            track_exception("agent_messages_realtime.subscribe", e)
            await asyncio.sleep(_RECONNECT_BACKOFF_SECONDS)
