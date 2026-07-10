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


# SHARED-AWARENESS feeds — read deliberately by every body, NEVER auto-drained
# from any single body's PERSONAL DM inbox (#24, Nazim carve-out call 2026-07-10):
#   war-room     = fleet room; all 3 read, respond-by-protocol (CAI-RESP-339:
#                  hub=ops/status/fleet, cai=governance, Nazim=console/CTO).
#   hafiz-partner= external partner group (Nazim-owned, NDA-gated).
# They get their OWN tag and are carved out of BOTH the hub and console personal
# reconciliation scopes, so a fleet/partner message never pollutes a DM inbox.
_SHARED_FEED_TAGS = ("war-room", "hafiz-partner")


def _shared_feed_exclusion() -> str:
    tags = ",".join("'%s'" % t for t in _SHARED_FEED_TAGS)
    return f" AND (tag IS NULL OR tag NOT IN ({tags}))"


def _channel_scope_sql() -> str:
    role = _body_role()
    if role == "console":
        # Nazim reconciles his OWN surfaces: in-console typing (channel
        # 'tmux-console') AND his private Telegram DM channel — @nazim_cto_bot,
        # which ingest logs as channel='telegram', tag='nazim-console'. Shared
        # feeds are excluded (a war-room/Hafiz msg is not a personal-DM nudge).
        return (" AND (channel='tmux-console' OR tag='nazim-console')"
                + _shared_feed_exclusion())
    if role == "hub":
        # Hub owns every operator surface EXCEPT the OTHER bodies' DMs (Nazim's
        # console @nazim_cto_bot, cai's @cai bot) and the shared feeds — so it
        # never answers another body's DM on the wrong bot/voice/pen, nor drains
        # a shared-awareness feed as a personal DM. (cai-channel was already
        # carved from mark_handled; carving it from the read scope too closes the
        # leak the operator's 2026-07-10 pipeline test exposed.)
        return (" AND channel<>'tmux-console'"
                " AND tag IS DISTINCT FROM 'nazim-console'"
                " AND tag IS DISTINCT FROM 'cai-channel'"
                + _shared_feed_exclusion())
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


def attach_transcript(msg_id: int, transcript: str) -> bool:
    """Enrich a logged voice-note row with its transcript, so the operator's
    actual WORDS are in the durable audit log — not just an audio-file pointer.
    Voice is a delivery layer on top of the text log, never a replacement."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_id(),))
        cur.execute(
            "UPDATE operator_messages SET text = text || ' | transcript: ' || %s "
            "WHERE id=%s AND text NOT LIKE '%% | transcript: %%'",
            (transcript, msg_id),
        )
        n = cur.rowcount
        conn.commit()
        return n > 0


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
        # Hub stamps must never eat another agent's channel: cai-channel rows
        # are cai's to handle (2026-07-05 — a blanket stamp nearly marked the
        # operator's message to cai as handled while cai was still booting).
        # unprocessed() intentionally still SHOWS them to the hub as a
        # visibility backstop; only the stamp is scoped away.
        scope = _channel_scope_sql()
        if _body_role() == "hub":
            scope += " AND channel IS DISTINCT FROM 'cai-channel'"
        cur.execute(
            "UPDATE operator_messages SET handled_at=now() "
            "WHERE direction='inbound' AND handled_at IS NULL AND id <= %s"
            + scope,
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
