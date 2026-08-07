"""responder_runner.py — the SEPARATE async drain unit for ai-responder channels
(CAI-RESP-357 amendment A2: transport/brains isolation).

ingest.py ends at LOG→GATE→ROUTE; conversational brains live HERE, in their own
process, reading the durable log. One hung persona call can therefore never dark
the operator channel — the exact failure class the unified design exists to kill.

Loop, per enabled ai-responder channel:
  1. fetch unhandled inbound rows for the channel_tag (operator_messages,
     handled_at IS NULL) — the deduped log row IS the queue (A1).
  2. dispatch to the channel's responder (responder_ref -> handler registry).
  3. enqueue the reply via tg_out (at-least-once outbound + audit).
  4. stamp handled_at (Option B: at-least-once; a rare re-surface beats a loss).

Handlers are registered by responder_ref. P3 ports the actual personas
(mamadah second-brain w/ pgvector recall, nutri-study) from the standalone bots;
until a handler is registered the channel's rows are LEFT UNSTAMPED and a
throttled line is logged — nothing is consumed by a brain that doesn't exist yet.

Run: .venv/bin/python3 -m nervous_system.responder_runner   (launchd: dev.wingmen.responder-runner)
"""
from __future__ import annotations

import os
import time
from typing import Callable

import psycopg
from dotenv import load_dotenv

from nervous_system import tg_out

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DRAIN_INTERVAL = 3
MISSING_HANDLER_LOG_EVERY = 300

# responder_ref -> handler(text, chat_id, conn) -> reply text (or None to stay silent)
HANDLERS: dict[str, Callable[[str, int | None, "psycopg.Connection"], str | None]] = {}


def register(ref: str):
    def deco(fn):
        HANDLERS[ref] = fn
        return fn
    return deco


def _dsn() -> str:
    return (os.environ.get("INGEST_DSN")
            or os.environ.get("DATABASE_URL")
            or os.environ.get("SUPABASE_DB_URL"))


def _log_line(msg: str) -> None:
    print(f"[responder] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def drain_once(conn) -> int:
    """One pass over all enabled ai-responder channels. Returns rows handled."""
    handled = 0
    with conn.cursor() as cur:
        cur.execute(
            "SELECT channel_key, channel_tag, responder_ref FROM bot_channels "
            "WHERE enabled AND mode='ai-responder'")
        chans = cur.fetchall()
    for key, tag, ref in chans:
        fn = HANDLERS.get(ref)
        if fn is None:
            _throttled_missing(key, ref)
            continue
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, chat_id, text FROM operator_messages "
                "WHERE direction='inbound' AND tag=%s AND handled_at IS NULL "
                "ORDER BY id LIMIT 10", (tag,))
            rows = cur.fetchall()
        for mid, chat_id, text in rows:
            try:
                reply = fn(text, int(chat_id) if chat_id else None, conn)
                if reply:
                    tg_out.enqueue(key, reply, chat_id=int(chat_id) if chat_id else None,
                                   reply_to=mid)
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
                    cur.execute("UPDATE operator_messages SET handled_at=now() WHERE id=%s", (mid,))
                conn.commit()
                handled += 1
            except Exception as e:
                # leave UNSTAMPED -> retried next pass (at-least-once); surface it
                _log_line(f"{key}: handler error on msg {mid}: {type(e).__name__}: {e}")
    return handled


_missing_last: dict[str, float] = {}


def _throttled_missing(key: str, ref: str) -> None:
    now = time.monotonic()
    if now - _missing_last.get(key, 0) > MISSING_HANDLER_LOG_EVERY:
        _log_line(f"{key}: no handler registered for responder_ref='{ref}' — rows left queued")
        _missing_last[key] = now


def main() -> int:
    _log_line(f"responder drain unit starting ({len(HANDLERS)} handler(s) registered)")
    while True:
        try:
            with psycopg.connect(_dsn()) as conn:
                drain_once(conn)
        except psycopg.Error as e:
            _log_line(f"WATCHDOG: substrate DB unreachable from responder_runner ({type(e).__name__})")
        time.sleep(DRAIN_INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
