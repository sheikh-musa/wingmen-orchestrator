"""operator_log.py — append a row to operator_messages (durable bridge log).

Both directions of the operator<->orchestrator Telegram bridge land here so the
conversation is never lost and stays coherent across the live-tmux and headless
incarnations of cc-orchestrator. The tg_send.sh helper logs every outbound reply;
tg_bridge.py logs every inbound message.
"""
from __future__ import annotations
import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


# --- ORCH-TOPOLOGY-001 body scoping -----------------------------------------
# Two orch bodies share this table (Studio hub + MacBook console/Nazim). Each
# body reconciles + stamps ONLY its own surface, enforced here (A3: in code
# that reads the env, not by promise): console sees channel='tmux-console'
# only; hub sees everything EXCEPT tmux-console (console messages are answered
# in-console by the console body). Unset role = legacy single-body behavior.

def _agent_id() -> str:
    return os.environ.get("ORCH_AGENT_ID", "cc-orchestrator")


def _body_role() -> str:
    return os.environ.get("ORCH_BODY_ROLE", "").strip().lower()


def _channel_scope_sql() -> str:
    role = _body_role()
    if role == "console":
        return " AND channel='tmux-console'"
    if role == "hub":
        return " AND channel<>'tmux-console'"
    return ""


def log(direction: str, text: str, chat_id: str | None = None,
        tag: str | None = None, delivered: bool = True,
        channel: str = "telegram") -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (direction, channel, chat_id, tag, text, delivered),
        )
        rid = cur.fetchone()[0]
        conn.commit()
        return rid


def recent(limit: int = 20) -> list:
    """Latest exchanges, oldest-first — the continuity context a fresh
    (headless or rebooted) cc-orchestrator reads to catch up."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT direction, tag, text, created_at FROM operator_messages "
            "ORDER BY id DESC LIMIT %s", (limit,))
        return list(reversed(cur.fetchall()))


# --- Option B: durable-log-as-source-of-truth (CAI-RESP-277) ---------------
# The keystroke injection is a best-effort NUDGE; the guarantee that an operator
# message is seen + answered lives HERE. Every inbound is logged with
# handled_at=NULL; cc-orchestrator reconciles by reading unprocessed() each turn
# / on the autonomous wakeup, answers, then stamps via mark_handled_through().
# At-least-once: a rare re-surfacing beats a silent loss (cai's ruling).

def unprocessed(limit: int = 20) -> list:
    """Inbound operator messages not yet marked handled, oldest-first. The
    reconciliation read that makes delivery independent of keystrokes landing."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, tag, text, created_at FROM operator_messages "
            "WHERE direction='inbound' AND handled_at IS NULL"
            + _channel_scope_sql() +
            " ORDER BY id ASC LIMIT %s", (limit,))
        return cur.fetchall()


def mark_handled_through(max_id: int) -> int:
    """Stamp every inbound up to and including max_id as handled. Called after
    cc-orchestrator has read + answered the operator's current messages. Returns
    the number of rows stamped."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        cur.execute(
            "UPDATE operator_messages SET handled_at=now() "
            "WHERE direction='inbound' AND handled_at IS NULL AND id <= %s"
            + _channel_scope_sql(),
            (max_id,))
        n = cur.rowcount
        conn.commit()
        return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("direction", choices=["inbound", "outbound"])
    ap.add_argument("text")
    ap.add_argument("--chat")
    ap.add_argument("--tag")
    ap.add_argument("--undelivered", action="store_true")
    ap.add_argument("--channel", default="telegram",
                    help="entry surface: telegram (default) | tmux-console | cockpit")
    a = ap.parse_args()
    print(log(a.direction, a.text, a.chat, a.tag, not a.undelivered, a.channel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
