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
from typing import Any

from nervous_system import agent_messages_poll
from nervous_system.error_tracker import track_exception

logger = logging.getLogger("wingmen.agent_messages_realtime")

_CHANNEL_NAME = "agent_messages_insert_v1"
_TABLE = "agent_messages"
_RECONNECT_BACKOFF_SECONDS = 30  # if subscribe() itself raises, wait this long


async def _route_single_message(
    supabase, bot, musa_chat_id: str | None, msg_id: int
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
    supabase, bot=None, musa_chat_id: str | None = None
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

    async def _on_insert(payload: dict) -> None:
        try:
            data = payload.get("data", {})
            record = data.get("record") or {}
            msg_id = record.get("id")
            if not isinstance(msg_id, int):
                logger.debug(f"realtime: INSERT payload without int id: {payload}")
                return
            await _route_single_message(supabase, bot, musa_chat_id, msg_id)
        except Exception as e:
            logger.error(f"realtime: _on_insert handler failed: {e}")
            track_exception("agent_messages_realtime.callback", e)

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

            # Block here forever; AsyncRealtimeClient.listen() processes events
            # and re-connects internally per its auto_reconnect setting.
            await realtime.listen()
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
