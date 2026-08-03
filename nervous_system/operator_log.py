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

from nervous_system import triage  # PASSIVE CoS triage annotation (read-only)

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


# --- Sender identity (BOT-INGEST-SENDER-001) --------------------------------
# ingest.py now records message.from into from_user_id / from_username /
# from_name. These helpers derive a human label + a source hint (DM vs group)
# so a reader can tell WHO sent a row — individuals within a group, not just
# the chat. Requires migration 020 applied (adds the three columns); the read
# functions below SELECT them, so run 020 before exercising this module.

def _musa_id() -> str:
    return os.environ.get("MUSA_TELEGRAM_ID", "").strip()


def _sender_label(from_user_id, from_name, from_username) -> str:
    """Human name for the sender: the operator himself → 'Musa'; else the
    Telegram first/last name; else @username; else 'unknown' (older rows / no
    message.from)."""
    musa = _musa_id()
    if from_user_id and musa and str(from_user_id) == musa:
        return "Musa"
    if from_name:
        return from_name
    if from_username:
        return "@" + str(from_username).lstrip("@")
    return "unknown"


# --- Passive CoS triage annotation (chief-of-staff spec, Step 1) -------------
# READ-ONLY: unprocessed()/recent() append a `triage` suggestion so Nazim can see
# the likely route when he reconciles. Prefer the stored cos_triage (computed by
# ingest at inbound time); fall back to recomputing from the text for legacy /
# NULL rows (deterministic — nothing is lost). This never routes or sends.

def _triage_for(stored, text, tag) -> dict:
    if isinstance(stored, dict) and stored:
        return stored
    try:
        return triage.classify(text, tag=tag).to_dict()
    except Exception:
        return {}


def _source_hint(chat_id, from_user_id) -> str:
    """'DM' when the message came from a private chat (positive chat_id, incl.
    the operator's own DM whose chat_id == MUSA_TELEGRAM_ID); 'group' for a
    negative group/supergroup chat_id. Unknown chat_id → 'group' (conservative:
    assume shared, don't mis-label as a private DM)."""
    musa = _musa_id()
    try:
        cid = int(chat_id) if chat_id is not None else None
    except (TypeError, ValueError):
        cid = None
    if cid is not None and cid > 0:
        return "DM"
    if from_user_id and musa and str(from_user_id) == musa and cid is not None and cid > 0:
        return "DM"
    return "group"


# SHARED-AWARENESS feeds — read deliberately by every body, NEVER auto-drained
# from any single body's PERSONAL DM inbox (#24, Nazim carve-out call 2026-07-10):
#   war-room     = fleet room; all 3 read, respond-by-protocol (CAI-RESP-339:
#                  hub=ops/status/fleet, cai=governance, Nazim=console/CTO).
#   hafiz-partner= external partner group (Nazim-owned, NDA-gated).
# They get their OWN tag and are carved out of BOTH the hub and console personal
# reconciliation scopes, so a fleet/partner message never pollutes a DM inbox.
_SHARED_FEED_TAGS = ("war-room", "hafiz-partner")

# Tags owned by a DEDICATED LANE AGENT, not by an orch body. Neither the hub nor
# the console reconciles these — the lane agent does, on its own tag, with its own
# reply path. Excluded from both bodies' scopes so a drill/lane thread can never be
# mistaken for an operator DM and answered on the wrong voice (2026-07-25, cc-irsyad
# build). `gazzabyte-irsyad` itself joins this list at cc-irsyad's direct cutover —
# NOT before: until then the hub still owns and answers the live client thread.
_LANE_OWNED_TAGS = ("irsyad-drill",)

# Suffixes written by the lane phase-gate (scripts/lane_reply.sh): '<tag>-drill' is a reply
# that never left the building, '<tag>-draft' is one awaiting a reviewer's send. Neither is
# ever an operator surface, for either body — excluded by shape so a new lane can't reintroduce
# the 2026-07-25 leak just by existing.
_LANE_TAG_SUFFIXES = ("-drill", "-draft")


def _shared_feed_exclusion() -> str:
    tags = ",".join("'%s'" % t for t in _SHARED_FEED_TAGS + _LANE_OWNED_TAGS)
    # '%%' — this SQL is passed to psycopg alongside %s parameters, so a literal percent
    # must be doubled or it is parsed as a (malformed) placeholder.
    suffixes = " AND ".join("tag NOT LIKE '%%" + s + "'" for s in _LANE_TAG_SUFFIXES)
    return f" AND (tag IS NULL OR (tag NOT IN ({tags}) AND {suffixes}))"


# Recognized body roles. ANY other value MUST fail CLOSED, never silently fall
# through to "" (an unscoped query). The 2026-07-05 incident (commit 3691cea):
# a misconfigured/typo'd ORCH_BODY_ROLE fell through and made
# mark_handled_through() stamp EVERY channel's inbound handled in ONE call —
# cross-body message loss. '' (unset) is the SANCTIONED legacy single-body
# behavior and stays permitted; a non-empty UNRECOGNIZED role raises loudly.
_RECOGNIZED_BODY_ROLES = frozenset({"console", "hub", ""})


def _channel_scope_sql() -> str:
    role = _body_role()
    if role not in _RECOGNIZED_BODY_ROLES:
        raise ValueError(
            "ORCH_BODY_ROLE=%r is not a recognized orch body role "
            "(expected 'console', 'hub', or empty). Refusing to build an "
            "UNSCOPED channel query: an unrecognized role must never silently "
            "let unprocessed()/mark_handled_through() span EVERY channel "
            "(cross-body message loss — see commit 3691cea, 2026-07-05)." % role
        )
    if role == "console":
        # Nazim reconciles his OWN surfaces: in-console typing (channel
        # 'tmux-console') AND his private Telegram DM channel — @nazim_cto_bot,
        # which ingest logs as channel='telegram', tag='nazim-console'. Shared
        # feeds are excluded (a war-room/Hafiz msg is not a personal-DM nudge).
        # PLUS the cosem/alderei channels his Mini console-ingest already POLLS
        # (boot_nazim_ingest.sh INGEST_CHANNELS): cosem-caai (Ray), cosem-exams
        # (Hariz), alderei (Nahar). Align-reconcile-to-ingest-ownership — whoever
        # polls a channel reconciles it, else the poll-here/reconcile-there split
        # forces the hub to relay every cosem inbound (2026-08-03, hub+console
        # co-signed; operator 'cosem = Nazim'). cosem-adcda is NOT here — the hub
        # ingest polls that one, so the hub keeps reconciling it.
        return (" AND (channel='tmux-console' OR tag='nazim-console'"
                " OR tag IN ('cosem-caai','cosem-exams','alderei'))"
                + _shared_feed_exclusion())
    if role == "hub":
        # Hub owns every operator surface EXCEPT the OTHER bodies' DMs (Nazim's
        # console @nazim_cto_bot, cai's @cai bot) and the shared feeds — so it
        # never answers another body's DM on the wrong bot/voice/pen, nor drains
        # a shared-awareness feed as a personal DM. (cai-channel was already
        # carved from mark_handled; carving it from the read scope too closes the
        # leak the operator's 2026-07-10 pipeline test exposed.)
        # cosem-caai/cosem-exams/alderei carved out too (2026-08-03): the Mini
        # console-ingest polls them, so the console reconciles them — the hub must
        # NOT also discover+relay them. IS DISTINCT FROM (not NOT IN) keeps
        # NULL-tag rows in hub scope.
        return (" AND channel<>'tmux-console'"
                " AND tag IS DISTINCT FROM 'nazim-console'"
                " AND tag IS DISTINCT FROM 'cai-channel'"
                " AND tag IS DISTINCT FROM 'cosem-caai'"
                " AND tag IS DISTINCT FROM 'cosem-exams'"
                " AND tag IS DISTINCT FROM 'alderei'"
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
    (headless or rebooted) cc-orchestrator reads to catch up.

    Return shape (BACKWARD-COMPATIBLE — first 4 positions unchanged, sender
    fields then the passive triage suggestion APPENDED at the end):
        (direction, tag, text, created_at,          # original 4
         from_user_id, from_username, from_name,    # raw message.from
         sender_label, source,                      # derived: 'Musa'/name, 'DM'/'group'
         triage)                                    # passive CoS route suggestion (dict), read-only
    """
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT direction, tag, text, created_at, chat_id, "
            "from_user_id, from_username, from_name, cos_triage FROM operator_messages "
            "ORDER BY id DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    out = []
    for (direction, tag, text, created_at, chat_id,
         fuid, funame, fname, cos_triage) in reversed(rows):
        out.append((direction, tag, text, created_at, fuid, funame, fname,
                    _sender_label(fuid, fname, funame),
                    _source_hint(chat_id, fuid),
                    _triage_for(cos_triage, text, tag)))
    return out


# --- Option B: durable-log-as-source-of-truth (CAI-RESP-277) ---------------
# The keystroke injection is a best-effort NUDGE; the guarantee that an operator
# message is seen + answered lives HERE. Every inbound is logged with
# handled_at=NULL; cc-orchestrator reconciles by reading unprocessed() each turn
# / on the autonomous wakeup, answers, then stamps via mark_handled_through().
# At-least-once: a rare re-surfacing beats a silent loss (cai's ruling).

def unprocessed(limit: int = 20) -> list:
    """Inbound operator messages not yet marked handled, oldest-first. The
    reconciliation read that makes delivery independent of keystrokes landing.

    Return shape (BACKWARD-COMPATIBLE — first 4 positions unchanged, sender
    fields then the passive triage suggestion APPENDED at the end):
        (id, tag, text, created_at,                 # original 4
         from_user_id, from_username, from_name,    # raw message.from
         sender_label, source,                      # derived: 'Musa'/name, 'DM'/'group'
         triage)                                    # passive CoS route suggestion (dict), read-only
    """
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, tag, text, created_at, chat_id, "
            "from_user_id, from_username, from_name, cos_triage FROM operator_messages "
            "WHERE direction='inbound' AND handled_at IS NULL"
            + _channel_scope_sql() +
            " ORDER BY id ASC LIMIT %s", (limit,))
        rows = cur.fetchall()
    return [(rid, tag, text, created_at, fuid, funame, fname,
             _sender_label(fuid, fname, funame),
             _source_hint(chat_id, fuid),
             _triage_for(cos_triage, text, tag))
            for (rid, tag, text, created_at, chat_id,
                 fuid, funame, fname, cos_triage) in rows]


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
