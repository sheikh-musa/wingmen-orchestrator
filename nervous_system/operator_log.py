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


def log(direction: str, text: str, chat_id: str | None = None,
        tag: str | None = None, delivered: bool = True) -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered) "
            "VALUES (%s,'telegram',%s,%s,%s,%s) RETURNING id",
            (direction, chat_id, tag, text, delivered),
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("direction", choices=["inbound", "outbound"])
    ap.add_argument("text")
    ap.add_argument("--chat")
    ap.add_argument("--tag")
    ap.add_argument("--undelivered", action="store_true")
    a = ap.parse_args()
    print(log(a.direction, a.text, a.chat, a.tag, not a.undelivered))
    return 0


if __name__ == "__main__":
    sys.exit(main())
