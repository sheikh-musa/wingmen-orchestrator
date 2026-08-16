#!/usr/bin/env python3
"""Apply migrations/048_lane_task_acceptance.sql via DIRECT psycopg.

NEVER `supabase db push` (decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001): the CLI's shadow-diff
path re-applies historic CREATE OR REPLACE VIEW bodies and silently strips later arms.

Target: the SUBSTRATE coordination-plane DB (DATABASE_URL), NOT any client silo.

Additive and reversible: four nullable columns + one view + comments. No data rewritten, no
constraint added, no status value invented (`lane_tasks_status_check` allows exactly
queued|active|done|blocked — inventing a token the CHECK rejects is what made 047's EXERCISED
state unreachable).

POST-CONDITIONS are checked INSIDE the transaction and roll back on failure. They assert the
view's PERMANENT invariants, never a transient fact about today's data — asserting "0 accepted
rows" would be true today and would become a rollback landmine the moment the first task is
legitimately accepted (that mistake, and its fix, is cc-quality's F2 on PR #74).

Usage:  scripts/apply_lane_task_acceptance.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "048_lane_task_acceptance.sql"


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

            # (1) THE DOCTRINE, executable: nothing may read as done without a real acceptance by
            #     somebody other than the doer.
            #
            #     cc-quality's sharpest finding on the first cut was not the bypass itself but
            #     that THIS GUARD SHARED IT: it re-implemented the normalisation
            #     (`acceptor_norm IS NOT DISTINCT FROM lane`) and so was blind in exactly the same
            #     spot -- a guard structurally incapable of catching the bug it exists to catch,
            #     which passed only because zero rows were accepted. It now CONSUMES the view's
            #     own `self_accepted` column instead of carrying a second copy of the rule. One
            #     definition, referenced twice; fixing the rule fixes the guard by construction.
            cur.execute(
                "SELECT count(*) FROM lane_tasks_state "
                "WHERE is_truly_done AND (accepted_at IS NULL "
                "                         OR accepted_by IS NULL "
                "                         OR status <> 'done' "
                "                         OR self_accepted)"
            )
            (false_done,) = cur.fetchone()
            if false_done:
                raise SystemExit(
                    f"post-condition FAILED: {false_done} row(s) read is_truly_done without a "
                    "valid non-self acceptance. Rolling back."
                )

            # (2) Label and boolean must never disagree — the same guarantee 047 needed, asserted
            #     at every apply so a future edit to one arm alone cannot ship.
            cur.execute(
                "SELECT count(*) FROM lane_tasks_state "
                "WHERE is_truly_done <> (completion_state = 'ACCEPTED')"
            )
            (disagreements,) = cur.fetchone()
            if disagreements:
                raise SystemExit(
                    f"post-condition FAILED: {disagreements} row(s) where is_truly_done disagrees "
                    "with completion_state='ACCEPTED'. Rolling back — boolean and label drifted."
                )

            # (3) No NULL bucket: every row carries a completion_state, and every 'done' row lands
            #     in exactly one of the three done-states. 047's F5 lesson — an unlabelled row is
            #     invisible to whoever is deciding what still needs attention.
            cur.execute(
                "SELECT count(*) FROM lane_tasks_state "
                "WHERE completion_state IS NULL "
                "   OR (status = 'done' AND completion_state NOT IN "
                "       ('ACCEPTED','SELF-ACCEPTED','CLAIMED-UNACCEPTED','ACCEPTED-BY-NOBODY')) "
                "   OR (status <> 'done' AND completion_state <> 'OPEN')"
            )
            (unlabelled,) = cur.fetchone()
            if unlabelled:
                raise SystemExit(
                    f"post-condition FAILED: {unlabelled} row(s) with a missing or wrong "
                    "completion_state. Rolling back."
                )

            # Informational only — NEVER asserted (that is what F2 was).
            cur.execute(
                "SELECT completion_state, count(*), count(*) FILTER (WHERE no_criteria) "
                "FROM lane_tasks_state GROUP BY 1 ORDER BY 2 DESC"
            )
            print("lane_tasks_state:")
            for state, n, nc in cur.fetchall():
                print(f"  {state:20} {n:4}   ({nc} with no acceptance criteria)")
        print("applied 048_lane_task_acceptance.sql")
        print("post-conditions OK: no done without non-self acceptance; label==boolean; no unlabelled rows")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
