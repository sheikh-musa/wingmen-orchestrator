#!/usr/bin/env python3
"""console_assign.py — assign a work item to a lane/coordinator from the fleet
console's drain board (fc-v52).

The console DB session is read-only by construction, so the console's
POST /api/assign shells out to THIS script (writable orchestrator env) to do the
write — the same vetted-script pattern /api/reset and backlog_swipe.py use.

What it does: inserts ONE real bus row into `agent_messages` addressed to the
target body (from_agent='orch-console', message_type='directive',
requires_response=true, is_test=false). Because it's a real, non-test inbox row,
the target body sees it in its normal inbox (`to_agent=<id> AND read_at IS NULL`)
and drains it exactly like any other work — so the console's drain board shrinks
as the body catches up. This is the "closes the loop" property the operator asked
for: an assignment is not a console-local note, it is fleet work on the bus.

Safety: the agent MUST already exist in `agents` (a console-assign can't invent a
recipient); an unknown agent exits 2 so the endpoint can return 400. The ask text
is passed as argv (never interpolated into SQL) and inserted as a bound param.

Usage:
    scripts/console_assign.py <agent_id> "<ask text>" [--priority P0|P1|P2]
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        # Load the orch .env if the caller didn't export it (launchd minimal env).
        try:
            from dotenv import load_dotenv  # type: ignore

            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            load_dotenv(os.path.join(here, ".env"))
            dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        except Exception:
            dsn = None
    if not dsn:
        sys.exit("DATABASE_URL not set")
    return dsn


def assign(agent: str, ask: str, priority: str) -> int:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agents WHERE id=%s", (agent,))
        if not cur.fetchone():
            # Distinct exit code so the endpoint maps it to 400 (bad agent), not 500.
            sys.stderr.write(f"unknown agent {agent!r}\n")
            sys.exit(2)
        subject = ask if len(ask) <= 80 else ask[:79].rstrip() + "…"
        body = (
            "Assigned from the fleet console drain board (operator).\n\n"
            f"{ask}\n\n"
            "Work it through your normal path; stamp read/responded when done so it "
            "leaves the board."
        )
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, "
            "body, priority, requires_response, is_test) "
            "VALUES ('orch-console', %s, 'directive', %s, %s, %s, true, false) "
            "RETURNING id",
            (agent, subject, body, priority),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
    return new_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", help="target bus id (lane base id or coordinator id)")
    ap.add_argument("ask", help="the work item text")
    ap.add_argument("--priority", default="P2", choices=("P0", "P1", "P2"))
    args = ap.parse_args()

    ask = (args.ask or "").strip()
    if not ask:
        sys.exit("empty ask")
    new_id = assign(args.agent.strip(), ask[:1000], args.priority)
    print(f"assigned agent_messages #{new_id} to {args.agent}")


if __name__ == "__main__":
    main()
