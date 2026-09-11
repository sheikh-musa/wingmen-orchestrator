#!/usr/bin/env python3
"""gazzabyte_request_nodrop_watch.py — no-drop safety net for Gazzabyte requests.

Musa (op 19790): "all requests from gazzabyte need to be followed up on without the
operators having to chase with repeated prompts." Coord owns irsyad client-comms end-to-end;
this watchdog is the MECHANISM that makes a coord drop loud — so the SYSTEM chases coord, not
the operator. Complements supervised_draft_deadman.py: that catches a reply DRAFTED-but-never-
released; THIS catches the other shape — the client spoke and got no reply/action at all.

SIGNAL: the gazzabyte-irsyad channel is one client thread. If the LATEST message on it is
INBOUND (the client spoke last) and older than the nudge window, the client is waiting and
nobody has replied — a drop.

ACTION TIERS (see evaluate()):
  - age > NUDGE_S: nudge COORD (cc-irsyad-coord) to follow up. Dedup per message + backoff.
  - age > ESCALATE_S AND coord was nudged on a PRIOR cycle: ALSO nudge CONSOLE (orch-console)
    — coord is sitting on it. This NEVER pages the operator (Musa): the whole point is that he
    is not the chaser. Console (an always-on agent) is the human-side backstop.

FAIL-CLOSED: a could-not-measure (DB error) nudges console LOUD and exits non-green — a safety
net that cannot read must never read green (dead-man's switch), same shape as the deadman.
UNGATED: detection + nudge are never lease-gated — a safety signal must not be silenceable.

Cadence: run periodically (launchd), like the other watchdogs. Idempotent + deduped, so a
short interval is safe.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import psycopg2

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ORCH, "state", "gazzabyte_request_nodrop_watch.json")

CHANNEL = os.environ.get("NODROP_CHANNEL", "gazzabyte-irsyad")
NUDGE_S = int(os.environ.get("NODROP_NUDGE_S", str(3 * 3600)))        # coord nudge after 3h unanswered
ESCALATE_S = int(os.environ.get("NODROP_ESCALATE_S", str(12 * 3600)))  # console backstop after 12h
RE_NUDGE_S = int(os.environ.get("NODROP_RE_NUDGE_S", str(4 * 3600)))   # re-nudge cadence for a standing drop

COORD_AGENT = "cc-irsyad-coord"
CONSOLE_AGENT = "orch-console"


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


def fetch_latest(dsn: str, channel: str) -> dict | None:
    """The most recent message on the channel, or None if the channel is empty.
    Raises CouldNotMeasure on any DB failure (fail-closed)."""
    try:
        with psycopg2.connect(dsn) as c, c.cursor() as cur:
            cur.execute(
                "SELECT id, direction, EXTRACT(EPOCH FROM created_at), from_name, LEFT(text, 240) "
                "FROM operator_messages WHERE tag=%s ORDER BY created_at DESC LIMIT 1",
                (channel,),
            )
            r = cur.fetchone()
    except Exception as e:  # noqa: BLE001 — any read failure is a fail-closed signal
        raise CouldNotMeasure(str(e))
    if not r:
        return None
    return {"id": int(r[0]), "direction": r[1], "created_epoch": float(r[2]),
            "from_name": r[3], "text": r[4] or ""}


def evaluate(latest, now, state, nudge_s=NUDGE_S, escalate_s=ESCALATE_S, re_nudge_s=RE_NUDGE_S):
    """Pure decision core (no I/O). Returns (actions, new_state).

    actions: list of {to, type, priority, subject, body}.
    new_state: tracks only the CURRENT latest inbound id — once the channel is answered
    (latest is outbound) the state clears, so a later re-drop re-nudges cleanly.
    """
    actions: list = []
    if not latest:
        return actions, {}  # empty channel — nothing waiting
    if latest["direction"] != "inbound":
        return actions, {}  # client is NOT waiting (someone replied last) — clear tracking

    age = now - latest["created_epoch"]
    if age < nudge_s:
        return actions, dict(state)  # client waiting but still within the reply window

    key = str(latest["id"])
    st = dict(state.get(key, {}))
    prior_coord = st.get("coord_nudged_at", 0)      # captured BEFORE this cycle acts
    prior_console = st.get("console_escalated_at", 0)
    mins = int(age // 60)
    who = latest.get("from_name") or "the client"
    preview = (latest.get("text") or "").strip().replace("\n", " ")[:160]

    # Tier 1 — nudge coord (dedup / backoff).
    if now - prior_coord >= re_nudge_s:
        actions.append({
            "to": COORD_AGENT, "type": "update", "priority": "P2",
            "subject": f"NO-DROP: Gazzabyte waiting {mins}m, unanswered (op#{latest['id']})",
            "body": (
                f"The gazzabyte-irsyad channel's LAST message is from {who} and has had NO reply "
                f"for {mins} minutes (operator_messages op#{latest['id']}). You own this loop — "
                f"follow up now: reply/act via `scripts/reviewer_send.sh {CHANNEL} \"<reply>\"`, and if "
                f"it is blocked, tell the client what it is blocked on so it does not go silent. "
                f"Log/close it in your request register.\n\n  last message: “{preview}”\n\n"
                f"This nudge repeats every {re_nudge_s // 3600}h until the channel is answered."
            ),
        })
        st["coord_nudged_at"] = now
        st["coord_nudge_count"] = st.get("coord_nudge_count", 0) + 1

    # Tier 2 — console backstop (coord is dropping): only once coord was nudged on a PRIOR cycle.
    if age >= escalate_s and prior_coord > 0 and (now - prior_console >= re_nudge_s):
        actions.append({
            "to": CONSOLE_AGENT, "type": "blocker", "priority": "P1",
            "subject": f"COORD DROPPING: Gazzabyte op#{latest['id']} unanswered {mins}m",
            "body": (
                f"A Gazzabyte request has been unanswered for {mins} minutes and coord was already "
                f"nudged on a prior cycle without clearing it (op#{latest['id']}). Coord is dropping the "
                f"loop — step in: confirm coord is alive/unwedged and get the client answered. This is the "
                f"console backstop; the operator is deliberately NOT paged by this watchdog.\n\n"
                f"  last message from {who}: “{preview}”"
            ),
        })
        st["console_escalated_at"] = now

    return actions, {key: st}


def _bus(dsn: str, action: dict) -> None:
    with psycopg2.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
            "priority, requires_response) VALUES ('gazzabyte-nodrop-watch',%s,%s,%s,%s,%s,true)",
            (action["to"], action["type"], action["subject"][:200], action["body"], action["priority"]),
        )
        c.commit()


def main() -> int:
    dsn = _dsn()
    now = time.time()
    try:
        latest = fetch_latest(dsn, CHANNEL)
    except CouldNotMeasure as e:
        _bus(dsn, {
            "to": CONSOLE_AGENT, "type": "blocker", "priority": "P1",
            "subject": "NO-DROP watch could-not-measure (fail-closed)",
            "body": (f"gazzabyte_request_nodrop_watch could NOT read {CHANNEL}: {e}\n"
                     "Treat as a possible unanswered Gazzabyte request and check the channel manually."),
        })
        print(f"could-not-measure -> paged console: {e}", file=sys.stderr)
        return 2

    state = _load_state()
    actions, new_state = evaluate(latest, now, state)
    for a in actions:
        _bus(dsn, a)
    _save_state(new_state)
    print(f"channel={CHANNEL} latest="
          f"{(latest or {}).get('direction')}#{(latest or {}).get('id')} actions={len(actions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
