"""front_desk_runner.py — the thin glue process for front desk v1 (op#29128,
bus #63359/#63381/#63383): instant-ack + a narrow "live list" whitelist-answer
in front of Nazim's nazim-console DMs. Same isolation pattern as
responder_runner.py (CAI-RESP-357 A2) — a separate process reading the durable
operator_messages log, never gating/blocking ingest.py's own path.

Per message (nazim-console inbound, handled_at IS NULL, front_desk_acked_at
IS NULL):
  1. classify(text, cos_triage) -> ANSWER or ACK (nervous_system.front_desk,
     pure, double-guarded: regex match AND cos_triage agrees AND not irsyad).
  2. ANSWER: query open operator_asks (ask_surface='operator', triage_state=
     'ask'), filter_irsyad(), render_live_list(), send via tg_out, stamp BOTH
     front_desk_acked_at AND handled_at in the same transaction (item 1 — a
     message can never get both an ack and a separate answer, because the two
     columns are set together, atomically, on the ANSWER branch only).
  3. ACK: send the fixed ACK_TEMPLATE via tg_out, stamp ONLY
     front_desk_acked_at — handled_at stays NULL so operator_log.unprocessed()
     keeps surfacing the row to Nazim, who still owns and answers it (item 1).
  4. Either branch: one front_desk_latency audit row.

KILL SWITCH (item 6): FRONT_DESK_ENABLED must be "1" (or "true") or main()
no-ops immediately without ever touching the DB — ingest.py/Nazim's own
reconciliation is then byte-for-byte what it was before this file existed,
because nothing here reads or writes anything when disabled (see
tests/test_front_desk_runner.py::test_main_noop_when_disabled and the parity
test alongside it).

Run: .venv/bin/python3 -m nervous_system.front_desk_runner
  (launchd: dev.wingmen.front-desk, mirrors dev.wingmen.responder-runner —
  WorkingDirectory the Mac Mini's live orchestrator checkout, same host as
  dev.wingmen.nazim-ingest / NAZIM_BOT_TOKEN, confirmed bus #63383 item 5.)
"""
from __future__ import annotations

import os
import time

import psycopg
from dotenv import load_dotenv

from nervous_system import front_desk, operator_log, tg_out

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DRAIN_INTERVAL = 3
CHANNEL_TAG = "nazim-console"
CHANNEL_KEY = "nazim-console"


def enabled() -> bool:
    return os.environ.get("FRONT_DESK_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _dsn() -> str:
    return (os.environ.get("INGEST_DSN")
            or os.environ.get("DATABASE_URL")
            or os.environ.get("SUPABASE_DB_URL"))


def _log_line(msg: str) -> None:
    print(f"[front-desk] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def _fetch_open_asks(conn) -> list[dict]:
    """Mirrors scripts/asks_daily_digest.py's fetch_open_asks() exactly —
    triage_summary-only, operator-surface-only, triaged-open-only. Kept as a
    separate copy rather than importing that script (a CLI entrypoint module,
    not a library) — same query, deliberately duplicated once."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, triage_summary, delegated_to, waiting_on_operator, chase_by, created_at "
            "FROM operator_asks WHERE closed_at IS NULL AND triage_state = 'ask' "
            "  AND ask_surface = 'operator' "
            "ORDER BY waiting_on_operator DESC, created_at ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def drain_once(conn) -> int:
    """One pass over unhandled, unacked nazim-console inbound rows. Returns
    the count of rows touched (ack or answer, combined)."""
    touched = 0
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, chat_id, text, cos_triage, created_at FROM operator_messages "
            "WHERE direction='inbound' AND tag=%s "
            "  AND handled_at IS NULL AND front_desk_acked_at IS NULL "
            "ORDER BY id LIMIT 20", (CHANNEL_TAG,)
        )
        rows = cur.fetchall()
    for mid, chat_id, text, cos_triage, received_at in rows:
        try:
            t = operator_log._triage_for(cos_triage, text, CHANNEL_TAG)
            kind = front_desk.classify(text, t)
            if kind == front_desk.ANSWER:
                asks = front_desk.filter_irsyad(_fetch_open_asks(conn))
                reply = front_desk.render_live_list(asks)
            else:
                reply = front_desk.ACK_TEMPLATE
            tg_out.enqueue(CHANNEL_KEY, reply, chat_id=int(chat_id) if chat_id else None,
                            reply_to=mid)
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
                if kind == front_desk.ANSWER:
                    cur.execute(
                        "UPDATE operator_messages SET front_desk_acked_at=now(), handled_at=now() "
                        "WHERE id=%s", (mid,))
                else:
                    cur.execute(
                        "UPDATE operator_messages SET front_desk_acked_at=now() WHERE id=%s", (mid,))
                cur.execute(
                    "INSERT INTO front_desk_latency (operator_message_id, kind, received_at) "
                    "VALUES (%s,%s,%s)", (mid, kind, received_at))
            conn.commit()
            touched += 1
        except Exception as e:
            # Leave UNSTAMPED -> retried next pass (at-least-once, same shape
            # as responder_runner.py). Nazim's own path is untouched either way.
            _log_line(f"handler error on msg {mid}: {type(e).__name__}: {e}")
    return touched


def main() -> int:
    if not enabled():
        _log_line("FRONT_DESK_ENABLED not set -- staying off, no DB touched")
        return 0
    _log_line("front desk starting")
    while True:
        try:
            with psycopg.connect(_dsn()) as conn:
                drain_once(conn)
        except psycopg.Error as e:
            _log_line(f"WATCHDOG: substrate DB unreachable from front_desk_runner ({type(e).__name__})")
        time.sleep(DRAIN_INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
