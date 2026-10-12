"""front_desk_runner.py — the thin glue process for front desk v1 (op#29128,
bus #63359/#63381/#63383, orch-console gate fixes bus #63427): a narrow
"live list" whitelist-answer in front of Nazim's nazim-console DMs. Same
isolation pattern as responder_runner.py (CAI-RESP-357 A2) — a separate
process reading the durable operator_messages log, never gating/blocking
ingest.py's own path.

SENDER/CHAT GATE (item 3, the most severe #63427 finding): nazim-console has
group chats mixed with Musa's own DMs. A front-desk reply can carry internal
fleet-ask summaries, so it must NEVER reach anyone but Musa in a private DM —
_is_musa_dm() gates on from_user_id == MUSA_TELEGRAM_ID AND chat_id being a
private chat (operator_log._source_hint() == "DM"), reusing the exact DM/group
classification ingest.py's own path already relies on. Any other sender/chat
shape is skipped entirely — no ack, no answer, no DB write; Nazim's own
reconciliation is completely untouched either way.

Per matching message (nazim-console inbound, handled_at IS NULL,
front_desk_acked_at IS NULL, Musa's own DM only):
  1. classify(text, cos_triage) -> ANSWER or ACK (nervous_system.front_desk,
     pure, double-guarded: regex match AND cos_triage agrees AND not irsyad).
  2. ANSWER: query open operator_asks (ask_surface='operator', triage_state=
     'ask'), filter_irsyad(), render_live_list(). Stamp front_desk_acked_at
     FIRST and commit, THEN send via tg_out (item 4 — if the send then fails,
     the row is already marked, so a retry next pass can never double-enqueue
     the same reply; the courtesy answer is simply lost once, not
     duplicated). handled_at is left untouched (item 7) — Nazim still
     reconciles every row; an auto-answer is a courtesy, not a resolution.
  3. ACK (not whitelisted): stamp front_desk_acked_at only, no Telegram send
     (item 5) — ingest.py already sends its own "📨 Got your message" ack on
     every nazim-console inbound row, so a second, front-desk-authored ack
     was a pure duplicate.
  4. Either branch: one front_desk_latency audit row, same transaction as the
     stamp.
  5. Any DB error during the stamp/audit transaction rolls back and leaves
     the row untouched for the next pass — no reply is ever sent for a row
     whose stamp failed (stamp happens before send, never after).

HOST/ROLE GUARD (item 1): main() refuses to start unless this is the Mac Mini
(this fleet has exactly one Mac — nervous_system/vault.py's _local_host_id()
convention) AND ORCH_BODY_ROLE == "console". Fail-closed: any error reading
either signal is treated as "not ok".

KILL SWITCH (item 2): FRONT_DESK_ENABLED is re-read every drain pass inside
the loop (_tick()), not once at startup — flipping it off takes effect within
one DRAIN_INTERVAL, no restart needed. Fail-closed: unreadable => disabled.

Run: .venv/bin/python3 -m nervous_system.front_desk_runner
  (launchd: dev.wingmen.front-desk — WorkingDirectory the Mac Mini's live
  orchestrator checkout, same host as dev.wingmen.nazim-ingest/
  NAZIM_BOT_TOKEN, confirmed bus #63383 item 5.)
"""
from __future__ import annotations

import os
import platform
import time

import psycopg
from dotenv import load_dotenv

from nervous_system import front_desk, operator_log, tg_out

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DRAIN_INTERVAL = 3
CHANNEL_TAG = "nazim-console"
CHANNEL_KEY = "nazim-console"


def enabled() -> bool:
    try:
        return os.environ.get("FRONT_DESK_ENABLED", "").strip().lower() in ("1", "true", "yes")
    except Exception:
        return False  # fail-closed: unreadable => disabled


def host_role_ok() -> tuple[bool, str]:
    """Fail-closed host/role guard (#63427 item 1). Any error reading either
    signal is treated as a refusal, never a pass-through."""
    try:
        system = platform.system()
    except Exception as e:
        return False, f"platform.system() unreadable: {type(e).__name__}: {e}"
    if system != "Darwin":
        return False, f"host check failed: platform.system()={system!r}, expected Darwin (the Mini)"
    try:
        role = os.environ.get("ORCH_BODY_ROLE", "").strip().lower()
    except Exception as e:
        return False, f"ORCH_BODY_ROLE unreadable: {type(e).__name__}: {e}"
    if role != "console":
        return False, f"host check failed: ORCH_BODY_ROLE={role!r}, expected 'console'"
    return True, ""


def _dsn() -> str:
    return (os.environ.get("INGEST_DSN")
            or os.environ.get("DATABASE_URL")
            or os.environ.get("SUPABASE_DB_URL"))


def _log_line(msg: str) -> None:
    print(f"[front-desk] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def _is_musa_dm(chat_id, from_user_id) -> bool:
    """Only Musa's own private DM may be acked/answered (#63427 item 3) —
    never a group chat, never a non-Musa sender. Reuses operator_log's own
    DM/group classification so this can't drift from the convention the
    rest of the ingest path already relies on. Most importantly: never the
    live list to a group, even one Musa happens to be posting from."""
    musa = os.environ.get("MUSA_TELEGRAM_ID", "").strip()
    if not musa or not from_user_id or str(from_user_id) != musa:
        return False
    return operator_log._source_hint(chat_id, from_user_id) == "DM"


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
    """One pass over unhandled, unacked nazim-console inbound rows from
    Musa's own DM only (_is_musa_dm — every other row is skipped outright,
    untouched). Returns the count of rows touched (ack or answer, combined)."""
    touched = 0
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, chat_id, from_user_id, text, cos_triage, created_at FROM operator_messages "
            "WHERE direction='inbound' AND tag=%s "
            "  AND handled_at IS NULL AND front_desk_acked_at IS NULL "
            "ORDER BY id LIMIT 20", (CHANNEL_TAG,)
        )
        rows = cur.fetchall()
    for mid, chat_id, from_user_id, text, cos_triage, received_at in rows:
        if not _is_musa_dm(chat_id, from_user_id):
            continue  # group chat or non-Musa sender: no ack, no answer, no write
        try:
            t = operator_log._triage_for(cos_triage, text, CHANNEL_TAG)
            kind = front_desk.classify(text, t)
            reply = None
            if kind == front_desk.ANSWER:
                asks = front_desk.filter_irsyad(_fetch_open_asks(conn))
                reply = front_desk.render_live_list(asks)

            # Stamp + audit FIRST, commit, THEN send (item 4) — a failed send
            # after this point loses one courtesy reply, it can never
            # double-send, because the row is already marked done.
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_agent_id',%s,true)",
                            (operator_log._agent_id(),))
                cur.execute(
                    "UPDATE operator_messages SET front_desk_acked_at=now() WHERE id=%s", (mid,))
                cur.execute(
                    "INSERT INTO front_desk_latency (operator_message_id, kind, received_at) "
                    "VALUES (%s,%s,%s)", (mid, kind, received_at))
            conn.commit()
            touched += 1

            if reply is not None:
                tg_out.enqueue(CHANNEL_KEY, reply, chat_id=int(chat_id) if chat_id else None,
                                reply_to=mid)
        except Exception as e:
            conn.rollback()
            # Leave UNSTAMPED -> retried next pass (at-least-once, same shape
            # as responder_runner.py). Nazim's own path is untouched either way.
            _log_line(f"handler error on msg {mid}: {type(e).__name__}: {e}")
    return touched


def _tick() -> None:
    """One loop iteration. The kill switch is re-read HERE, every pass (item
    2) — not just once at startup, so flipping FRONT_DESK_ENABLED off takes
    effect within one DRAIN_INTERVAL without a restart. Looks up
    psycopg.connect at call time (not a bound default) so tests can
    monkeypatch it."""
    if not enabled():
        return
    try:
        with psycopg.connect(_dsn()) as conn:
            drain_once(conn)
    except psycopg.Error as e:
        _log_line(f"WATCHDOG: substrate DB unreachable from front_desk_runner ({type(e).__name__})")


def main() -> int:
    ok, reason = host_role_ok()
    if not ok:
        _log_line(f"REFUSING to start: {reason}")
        return 1
    _log_line("front desk starting")
    while True:
        _tick()
        time.sleep(DRAIN_INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
