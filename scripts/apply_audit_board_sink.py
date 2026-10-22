#!/usr/bin/env python3
"""Apply migrations/055_audit_board_sink.sql via DIRECT psycopg.

cc-quality F2 / cai CAI-RESP-992 item 4d: the audit board had no reader.

NEVER `supabase db push` (decision 962). Target: the SUBSTRATE coordination-plane DB.

THE DIGEST IS FIRED FOR REAL, NOT IN A SAVEPOINT. cai's whole 4d point is that a measurer with no
sink is invisible, and the fleet has now been bitten three times by controls that were registered
and never executed (pipeline_clock never scheduled; the challenge window never firing; jobid 10
with zero runs at the time cc-quality audited it). "Scheduled and active" is a schedule, not an
execution. So this runs it once, live, and prints the bus row it produced -- and it asserts the
cron job exists AND is active on top of that.

Usage:  scripts/apply_audit_board_sink.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "055_audit_board_sink.sql"


def main() -> int:
    load_dotenv(ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL not set", file=sys.stderr)
        return 2

    sql = SQL_PATH.read_text()
    if "--dry-run" in sys.argv:
        print(f"dry-run: would apply {SQL_PATH.name} ({len(sql)} bytes)")
        return 0

    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql)

            # (1) SCHEDULED AND ACTIVE — the pipeline_clock post-condition.
            cur.execute(
                "SELECT jobid, schedule, active FROM cron.job WHERE jobname='audit-board-digest'"
            )
            job = cur.fetchone()
            if not job:
                raise SystemExit(
                    "post-condition FAILED: no cron.job row for audit-board-digest. An unscheduled "
                    "sink is pipeline_clock again. Rolling back."
                )
            if not job[2]:
                raise SystemExit(f"post-condition FAILED: job {job[0]} active=false. Rolling back.")
            print(f"  scheduled: cron jobid={job[0]} schedule={job[1]!r} active={job[2]}")

            # (2) FIRE IT FOR REAL. Registered-but-never-executed is the defect of the night.
            cur.execute("SELECT had_content, summary FROM audit_board_digest()")
            had, summary = cur.fetchone()
            print(f"  EXECUTED: had_content={had} summary={summary!r}")

            # (3) A run must be LOGGED whether or not it had anything to say — that is what makes
            #     the digest's own silence readable.
            cur.execute("SELECT count(*) FROM audit_board_digest_log")
            (runs,) = cur.fetchone()
            if runs < 1:
                raise SystemExit("post-condition FAILED: the run was not logged. Rolling back.")

            # (4) If it had content it must have actually REACHED somebody. A digest that computes
            #     a summary and posts nowhere is the exact defect being fixed.
            if had:
                cur.execute(
                    "SELECT m.id, m.to_agent, m.subject FROM audit_board_digest_log l "
                    "JOIN agent_messages m ON m.id = l.msg_id "
                    "ORDER BY l.id DESC LIMIT 1"
                )
                row = cur.fetchone()
                if not row:
                    raise SystemExit(
                        "post-condition FAILED: had_content=true but no bus row was written. "
                        "Rolling back."
                    )
                print(f"  DELIVERED: bus #{row[0]} -> {row[1]}")
                print(f"    {row[2]}")
                cur.execute("SELECT body FROM agent_messages WHERE id=%s", (row[0],))
                print("--- digest body (read it; that is the point) ---")
                print(cur.fetchone()[0])
            else:
                print("  board is clean — no bus row, by design (a daily all-clear trains people "
                      "to stop reading)")

            # (5) Not anon-reachable, same posture as everything else touched tonight.
            for role in ("anon", "authenticated"):
                cur.execute(
                    "SELECT has_function_privilege(%s,'public.audit_board_digest()','EXECUTE')", (role,)
                )
                if cur.fetchone()[0]:
                    raise SystemExit(f"post-condition FAILED: {role} can EXECUTE the digest. Rolling back.")
                cur.execute("SELECT has_table_privilege(%s,'audit_board_digest_log','SELECT')", (role,))
                if cur.fetchone()[0]:
                    raise SystemExit(f"post-condition FAILED: {role} can read the digest log. Rolling back.")
            print("  posture: anon/authenticated cannot execute the digest or read its log")

        print("applied 055_audit_board_sink.sql")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
