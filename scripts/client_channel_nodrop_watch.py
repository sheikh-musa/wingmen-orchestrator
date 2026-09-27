#!/usr/bin/env python3
"""client_channel_nodrop_watch.py — no-drop safety net for CLIENT SUPPORT channels.

WHY (Nazim 43625/43631): a client operator (Fazli/TDU) posted in the cosem-tdu group at
2026-09-26 17:37Z and got NO real reply for ~12h (op#22517) — nothing paged. The Gazzabyte
no-drop watch only covers gazzabyte-irsyad. This generalizes the SAME pattern to every client
support channel, with tighter windows (a client is a paying customer).

SIGNAL (per channel): find the latest INBOUND (the client's last message). It is UNANSWERED
unless a NON-AUTOMATED outbound exists after it. The ingest auto-posts a "📨 Got your message"
RECEIPT into client groups (ingest.py reassure_if_unhandled, which itself dedups on
LIKE '%Got your message%'); a receipt is NOT a real reply and must NOT clear the drop — that
was the exact hole this watch exists to catch (Nazim 43631). is_receipt() matches that marker.

ACTION TIERS (evaluate(), per channel):
  - unanswered > nudge_s (default 30m): nudge the channel's COORD (with its send script).
  - unanswered > escalate_s (default 2h) AND coord was nudged on a PRIOR cycle: ALSO page
    orch-console (Nazim). The operator (Musa) is NEVER paged.

CHANNELS: DERIVED from bot_channels (mode=agent-session, group_routing.agent_reviewer = a
`cc-*` coord), so a new client group is covered automatically once its bot_channel exists
(Nazim 43631). gazzabyte-irsyad is EXCLUDED (its dedicated watch owns it, different windows).
Fail-safe: if bot_channels can't be read, fall back to the known static cosem-tdu entry.

STAGED ARMING: DETECT-ONLY by default (logs what it WOULD page, writes NOTHING). --arm posts
the bus nudges. FAIL-CLOSED: a could-not-measure on a channel pages orch-console LOUD.
UNGATED (a safety signal is never lease-gated). Cadence: launchd; idempotent + deduped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import psycopg2

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ORCH, "state", "client_channel_nodrop_watch.json")

CONSOLE_AGENT = "orch-console"
NUDGE_S = int(os.environ.get("CLIENT_NODROP_NUDGE_S", str(30 * 60)))         # coord nudge after 30m
ESCALATE_S = int(os.environ.get("CLIENT_NODROP_ESCALATE_S", str(2 * 3600)))  # console/Nazim after 2h
RE_NUDGE_S = int(os.environ.get("CLIENT_NODROP_RE_NUDGE_S", str(60 * 60)))   # re-nudge cadence
# Live-drop horizon (Nazim 43634): an inbound older than this is NOT a live drop (a nudge
# won't help — it's backlog / done-but-unacked). It is reported ONCE per message id as
# 'stale backlog' to console (no coord nudge, no repeats), so an ancient drop is still seen once.
MAX_LIVE_S = int(os.environ.get("CLIENT_NODROP_MAX_LIVE_S", str(7 * 86400)))  # 7 days
FETCH_LIMIT = 25  # recent rows to scan per channel (plenty to see the reply-after-inbound)

# Channels covered by OTHER mechanisms — never double-watch.
EXCLUDE_TAGS = {"gazzabyte-irsyad"}  # dedicated gazzabyte_request_nodrop_watch.py (3h/12h)
# Per-channel sanctioned send script (the coord's reply path), by tag. Unknown -> generic hint.
SEND_SCRIPTS = {
    "cosem-tdu": "scripts/cosem_tdu_support_send.sh",
    "gazzabyte-irsyad": "scripts/reviewer_send.sh",
}
# Fail-safe fallback if bot_channels can't be read: the known-live client channel.
FALLBACK_CHANNELS = [
    {"tag": "cosem-tdu", "coord": "cc-cosem-tdu-coord", "label": "cosem-tdu",
     "send_script": SEND_SCRIPTS["cosem-tdu"]},
]


class CouldNotMeasure(Exception):
    pass


def _dsn() -> str:
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    m = re.search(r"^DATABASE_URL=(.+)$", open(os.path.join(ORCH, ".env")).read(), re.M)
    if not m:
        raise SystemExit("DATABASE_URL not found")
    return m.group(1).strip()


def _load_state() -> dict:
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE_PATH)


# ---- pure helpers (unit-tested; no I/O) ---------------------------------------
def is_receipt(text: "str | None") -> bool:
    """True for the ingest's auto-ack receipt ('📨 Got your message …'). Source of truth:
    ingest.py posts these and reassure_if_unhandled dedups on LIKE '%Got your message%'."""
    return "got your message" in (text or "").lower()


def latest_unanswered_inbound(rows):
    """rows = recent messages for ONE channel, NEWEST-FIRST, each
    {id, direction, created_epoch, from_name, text}. Return the latest INBOUND that has NOT
    been answered by a NON-RECEIPT outbound after it, or None (answered / empty / not-waiting).
    A '📨 Got your message' receipt does NOT count as an answer (Nazim 43631)."""
    # newest-first: find the first (latest) inbound
    for i, r in enumerate(rows):
        if r["direction"] == "inbound":
            # any row newer than it (earlier in the list) that is a real (non-receipt) outbound?
            for newer in rows[:i]:
                if newer["direction"] == "outbound" and not is_receipt(newer.get("text")):
                    return None  # answered by a real reply
            return r  # latest inbound, only receipts (if any) after it -> UNANSWERED
    return None  # no inbound at all


def evaluate(waiting, now, state, *, coord, label, tag, send_script,
             nudge_s=NUDGE_S, escalate_s=ESCALATE_S, re_nudge_s=RE_NUDGE_S, max_live_s=MAX_LIVE_S):
    """PURE decision core for ONE channel. `waiting` = the unanswered inbound (or None).
    Returns (actions, new_state). Operator is NEVER a target (coord + console only)."""
    actions: list = []
    if not waiting:
        return actions, {}                      # answered / empty — clear tracking
    age = now - waiting["created_epoch"]
    if age < nudge_s:
        return actions, dict(state)             # waiting but still within the reply window

    key = str(waiting["id"])
    st = dict(state.get(key, {}))
    mins = int(age // 60)
    who = waiting.get("from_name") or "the client"
    preview = (waiting.get("text") or "").strip().replace("\n", " ")[:160]

    # Stale backlog (Nazim 43634): older than the live horizon is NOT a live drop — report it
    # ONCE to console (no coord nudge, no repeats), so an ancient drop is seen but never spams.
    if age > max_live_s:
        if not st.get("stale_reported_at"):
            days = int(age // 86400)
            actions.append({
                "to": CONSOLE_AGENT, "type": "update", "priority": "P2",
                "subject": f"STALE BACKLOG: {label} op#{waiting['id']} unanswered ~{days}d (one-time, no page)",
                "body": (
                    f"The {tag} channel has an UNANSWERED client message ~{days} days old "
                    f"(op#{waiting['id']}, from {who}) — older than the {max_live_s // 86400}d live-drop "
                    f"horizon, so it is NOT nudged as a live drop (a nudge won't help). Reporting it ONCE "
                    f"so it is seen: likely done-but-unacked or a defunct thread — check + ack/close it. No "
                    f"coord nudge, no repeats for this message.\n\n  last message: “{preview}”"
                ),
            })
            st["stale_reported_at"] = now
        return actions, {key: st}

    prior_coord = st.get("coord_nudged_at", 0)
    prior_console = st.get("console_escalated_at", 0)

    if now - prior_coord >= re_nudge_s:
        actions.append({
            "to": coord, "type": "update", "priority": "P2",
            "subject": f"NO-DROP: {label} client waiting {mins}m, unanswered (op#{waiting['id']})",
            "body": (
                f"The {tag} client channel's last CLIENT message is from {who} and has had NO real "
                f"reply for {mins} minutes (operator_messages op#{waiting['id']}); an auto-receipt does "
                f"NOT count. You own this client loop — reply/act now via `{send_script} \"<reply>\"`, and "
                f"if it is blocked, tell the client what it is blocked on so the chat does not go silent. "
                f"Log/close it in your register.\n\n  last message: “{preview}”\n\n"
                f"This repeats every {re_nudge_s // 3600 or 1}h until answered."
            ),
        })
        st["coord_nudged_at"] = now
        st["coord_nudge_count"] = st.get("coord_nudge_count", 0) + 1

    if age >= escalate_s and prior_coord > 0 and (now - prior_console >= re_nudge_s):
        actions.append({
            "to": CONSOLE_AGENT, "type": "blocker", "priority": "P1",
            "subject": f"COORD DROPPING: {label} op#{waiting['id']} unanswered {mins}m",
            "body": (
                f"A {tag} client request has been unanswered for {mins} minutes and its coord ({coord}) "
                f"was already nudged on a prior cycle without clearing it (op#{waiting['id']}) — an "
                f"auto-receipt does not count as answered. Coord is dropping the client loop — step in: "
                f"confirm coord is alive/unwedged and get the client answered. Console backstop; the "
                f"operator is deliberately NOT paged by this watch.\n\n  last message from {who}: “{preview}”"
            ),
        })
        st["console_escalated_at"] = now

    return actions, {key: st}


# ---- channel discovery + fetch ------------------------------------------------
def load_channels(dsn) -> list:
    """Derive client support channels from bot_channels: mode=agent-session with a `cc-*`
    coord in group_routing.agent_reviewer (a real coord, not orch-console), minus EXCLUDE_TAGS
    (gazzabyte has its own watch). Fail-safe: any read error -> FALLBACK_CHANNELS."""
    try:
        with psycopg2.connect(dsn) as c, c.cursor() as cur:
            cur.execute(
                "SELECT channel_tag, group_routing->>'agent_reviewer' "
                "FROM bot_channels WHERE mode='agent-session' "
                "AND group_routing->>'agent_reviewer' LIKE 'cc-%'"
            )
            rows = cur.fetchall()
    except Exception:  # noqa: BLE001 — fail-safe: still watch the known channel
        return list(FALLBACK_CHANNELS)
    out = []
    for tag, coord in rows:
        if tag in EXCLUDE_TAGS or not coord:
            continue
        out.append({"tag": tag, "coord": coord, "label": tag,
                    "send_script": SEND_SCRIPTS.get(tag, "your sanctioned client-send path")})
    return out or list(FALLBACK_CHANNELS)


def fetch_recent(dsn: str, tag: str, limit: int = FETCH_LIMIT) -> list:
    """Recent messages for a channel, NEWEST-FIRST. Raises CouldNotMeasure on any DB failure."""
    try:
        with psycopg2.connect(dsn) as c, c.cursor() as cur:
            cur.execute(
                "SELECT id, direction, EXTRACT(EPOCH FROM created_at), from_name, LEFT(text, 240) "
                "FROM operator_messages WHERE tag=%s ORDER BY created_at DESC LIMIT %s",
                (tag, limit),
            )
            rs = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — fail-closed
        raise CouldNotMeasure(str(e))
    return [{"id": int(r[0]), "direction": r[1], "created_epoch": float(r[2]),
             "from_name": r[3], "text": r[4] or ""} for r in rs]


def _bus(dsn: str, action: dict) -> None:
    with psycopg2.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
            "priority, requires_response) VALUES ('client-nodrop-watch',%s,%s,%s,%s,%s,true)",
            (action["to"], action["type"], action["subject"][:200], action["body"], action["priority"]),
        )
        c.commit()


def _fail_closed_page(dsn, tag, err, armed):
    action = {
        "to": CONSOLE_AGENT, "type": "blocker", "priority": "P1",
        "subject": f"CLIENT NO-DROP watch could-not-measure ({tag}) fail-closed",
        "body": (f"client_channel_nodrop_watch could NOT read {tag}: {err}\n"
                 "Treat as a possible unanswered client request and check the channel manually."),
    }
    if armed:
        _bus(dsn, action)
    print(f"[{tag}] could-not-measure -> {'PAGED console' if armed else 'WOULD page (detect-only)'}: {err}",
          file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", action="store_true",
                    help="actually post the bus nudges. Default: DETECT-ONLY (log only, write nothing).")
    args = ap.parse_args(argv)
    armed = args.arm
    mode = "ARMED" if armed else "DETECT-ONLY"

    dsn = _dsn()
    now = time.time()
    state = _load_state()
    new_state = dict(state)
    total_actions = 0
    had_error = False
    channels = load_channels(dsn)

    for ch in channels:
        tag = ch["tag"]
        try:
            rows = fetch_recent(dsn, tag)
        except CouldNotMeasure as e:
            had_error = True
            _fail_closed_page(dsn, tag, str(e), armed)
            continue
        waiting = latest_unanswered_inbound(rows)
        actions, ns = evaluate(waiting, now, state.get(tag, {}), coord=ch["coord"],
                               label=ch["label"], tag=tag, send_script=ch["send_script"])
        new_state[tag] = ns
        for a in actions:
            total_actions += 1
            verb = "PAGED" if armed else "WOULD page"
            if armed:
                _bus(dsn, a)
            print(f"[{tag}] {verb} {a['to']} ({a['priority']}): {a['subject']}")
        if not actions:
            print(f"[{tag}] coord={ch['coord']} waiting={'op#' + str(waiting['id']) if waiting else 'none'} — no action")

    if armed:
        _save_state(new_state)
    print(f"[client-nodrop {mode}] channels={len(channels)} actions={total_actions} errors={int(had_error)}")
    return 2 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
