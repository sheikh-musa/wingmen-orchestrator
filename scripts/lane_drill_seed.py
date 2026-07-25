#!/usr/bin/env python3
"""lane_drill_seed.py — seed a DRILL for a client-facing lane agent, safely.

WHY THIS EXISTS (2026-07-25 incident): cc-irsyad's first drill was seeded as realistic
fake client messages into `operator_messages` under tag 'irsyad-drill'. The hub reads that
table more broadly than the tag filter in `operator_log`, picked them up as real client
messages, and answered them into the LIVE Gazzabyte client group. Nobody was at fault for
reading a shared table — the harness was.

THE FIX, structural rather than filter-based: drill traffic never enters operator_messages.
It is seeded as `agent_messages` rows addressed to the lane agent ONLY, so no other body
can see it at all. Every row is stamped is_test=true and its subject/body are prefixed with
an unmissable DRILL marker, so even a misrouted read cannot mistake it for a client.

Usage:
    scripts/lane_drill_seed.py cc-irsyad drills/irsyad-round2.json
    scripts/lane_drill_seed.py cc-irsyad --announce --note "..."   # BEFORE you seed

The drill file is a JSON list of objects: [{"from": "Gazzabyte group", "text": "..."}, ...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg

MARKER = "[DRILL — SYNTHETIC. Not from a client. Seeded by Nazim's drill harness.]"


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.exit("DATABASE_URL not set")
    return dsn


def seed(agent: str, items: list[dict]) -> list[int]:
    ids: list[int] = []
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agents WHERE id=%s", (agent,))
        if not cur.fetchone():
            sys.exit(f"unknown agent {agent!r} — register it before drilling it")
        for i, item in enumerate(items, 1):
            body = (f"{MARKER}\n\nSimulated inbound from: {item.get('from','the client')}\n"
                    f"Work it exactly as if it were real, and reply through your normal "
                    f"phase-gated reply path.\n\n--- message ---\n{item['text']}")
            cur.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, "
                "body, priority, requires_response, is_test) "
                "VALUES ('orch-console',%s,'question',%s,%s,'P2',true,true) RETURNING id",
                (agent, f"DRILL {i}/{len(items)} — synthetic client message (NOT real)", body))
            ids.append(cur.fetchone()[0])
        conn.commit()
    return ids


def announce(agent: str, note: str) -> int:
    """Tell the hub a drill is starting. The step whose absence caused the incident."""
    body = (f"Heads-up: I am running a DRILL against {agent} starting now.\n\n{note}\n\n"
            "Drill traffic is seeded as agent_messages addressed to that agent only "
            "(is_test=true) — it does NOT enter operator_messages, so nothing should reach "
            "your inbox. If you DO see anything that reads like a client message about this, "
            "treat it as synthetic and verify provenance before any client-facing send.")
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
            "priority) VALUES ('orch-console','cc-orchestrator','update',%s,%s,'P2') RETURNING id",
            (f"DRILL STARTING against {agent} — synthetic traffic, do not act on it", body))
        mid = cur.fetchone()[0]
        conn.commit()
    return mid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", help="lane agent id, e.g. cc-irsyad")
    ap.add_argument("drill_file", nargs="?", help="JSON list of {from, text}")
    # A flag + separate --note: an optional-value option would swallow the drill_file
    # positional ("--announce 'text' file.json" parses as note='text', file unrecognized).
    ap.add_argument("--announce", action="store_true",
                    help="announce the drill to the hub (do this BEFORE seeding)")
    ap.add_argument("--note", default="Routine lane drill.",
                    help="text of the announcement")
    args = ap.parse_args()

    if args.announce:
        print("announced to hub as agent_messages #%d" % announce(args.agent, args.note))
    if args.drill_file:
        items = json.load(open(args.drill_file))
        if not isinstance(items, list) or not all("text" in i for i in items):
            sys.exit("drill file must be a JSON list of objects with a 'text' key")
        print("seeded drill rows:", seed(args.agent, items))


if __name__ == "__main__":
    main()
