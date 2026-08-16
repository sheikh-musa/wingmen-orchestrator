#!/usr/bin/env python3
"""console_assign.py — assign a work item to a lane/coordinator from the fleet
console's drain board (fc-v52).

The console DB session is read-only by construction, so the console's
POST /api/assign shells out to THIS script (writable orchestrator env) to do the
write — the same vetted-script pattern /api/reset and backlog_swipe.py use.

What it does, in ONE transaction:
  (1) inserts ONE real bus row into `agent_messages` addressed to the target body
      (from_agent='orch-console', message_type='decision', requires_response=true,
      is_test=false) with a fresh `thread_id` (gen_random_uuid()). Because it's a
      real, non-test inbox row, the target body sees it in its normal inbox
      (`to_agent=<id> AND read_at IS NULL`) and drains it exactly like any other
      work — so the console's drain board shrinks as the body catches up.
  (2) writes ONE link row into `operator_asks` (migration 044) storing that SAME
      `thread_id`, the ask text, the delegated agent, and the originating
      operator_messages id (if the assign was raised from a specific inbound ask).
      operator_asks NEVER stores status — the "Your asks" board derives status LIVE
      from the linked agent_messages thread on every poll, so it cannot go stale
      (op#13250: "this cannot be stale info").

Both writes commit together: an assignment is not a console-local note, it is
fleet work on the bus AND a tracked operator ask, atomically.

Safety: the agent MUST already exist in `agents` (a console-assign can't invent a
recipient); an unknown agent exits 2 so the endpoint can return 400. The ask text
is passed as argv (never interpolated into SQL) and inserted as a bound param.

Usage:
    scripts/console_assign.py <agent_id> "<ask text>" [--priority P0|P1|P2]
                              [--source-msg-id <operator_messages.id>]
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


def assign(agent: str, ask: str, priority: str, source_msg_id: "int | None" = None) -> int:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agents WHERE id=%s", (agent,))
        if not cur.fetchone():
            # Distinct exit code so the endpoint maps it to 400 (bad agent), not 500.
            sys.stderr.write(f"unknown agent {agent!r}\n")
            sys.exit(2)
        subject = ask if len(ask) <= 80 else ask[:79].rstrip() + "…"
        body = (
            "Assigned from the fleet console (operator ask).\n\n"
            f"{ask}\n\n"
            "Work it through your normal path; reply ON THIS THREAD (same thread_id) "
            "and stamp read/responded when done so the operator's 'Your asks' board "
            "moves it to review. If you need the operator's decision, reply back to "
            "orch-console with requires_response=true — that surfaces it as 'needs you'."
        )
        # thread_id is a fresh uuid (agent_messages.thread_id is uuid, set via
        # gen_random_uuid() elsewhere — fleet_stall_watch.py / watchdog_tripwire).
        # The lane's reply rides the SAME thread_id, which is exactly what the
        # "Your asks" board joins on to derive live status. RETURNING both so the
        # link row stores the same uuid.
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, thread_id, message_type, "
            "subject, body, priority, requires_response, is_test) "
            # message_type MUST be one of agent_messages_message_type_check's eight
            # values. It shipped as 'directive' — which is NOT one of them, so EVERY
            # console assign has raised CheckViolation since fc-v52 and the operator's
            # assign button has never once written a row (found 2026-08-16 by using it).
            # 'decision' is the closest allowed type for an instruction that expects a
            # reply; do NOT reintroduce a value the constraint does not allow.
            "VALUES ('orch-console', %s, gen_random_uuid(), 'decision', %s, %s, %s, true, false) "
            "RETURNING id, thread_id",
            (agent, subject, body, priority),
        )
        new_id, thread_id = cur.fetchone()
        # SAME-TRANSACTION link row (migration 044). Status is NEVER stored here;
        # the board derives it live from the thread above. If operator_asks isn't
        # applied yet, this raises and the whole tx rolls back (no orphan bus row).
        cur.execute(
            "INSERT INTO operator_asks (ask, source_msg_id, thread_id, delegated_to) "
            "VALUES (%s, %s, %s, %s)",
            (ask, source_msg_id, thread_id, agent),
        )
        conn.commit()
    return new_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", help="target bus id (lane base id or coordinator id)")
    ap.add_argument("ask", help="the work item text")
    ap.add_argument("--priority", default="P2", choices=("P0", "P1", "P2"))
    ap.add_argument("--source-msg-id", type=int, default=None,
                    help="operator_messages.id this ask was raised from (optional)")
    args = ap.parse_args()

    ask = (args.ask or "").strip()
    if not ask:
        sys.exit("empty ask")
    # Truncation is capped, but say so — an assignment silently losing its last
    # paragraph reads to the lane as a complete instruction that was never given.
    if len(ask) > 1000:
        sys.stderr.write(
            f"warning: ask is {len(ask)} chars, truncating to 1000 — "
            f"dropped tail: {ask[1000:][:200]!r}\n"
        )
    new_id = assign(args.agent.strip(), ask[:1000], args.priority, args.source_msg_id)
    print(f"assigned agent_messages #{new_id} to {args.agent}")


if __name__ == "__main__":
    main()
