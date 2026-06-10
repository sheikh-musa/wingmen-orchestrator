"""SUBSTRATE-COHERENCE-001 D plumbing (cai #2001): allow status='archived'.

cc-scholar's reconciliation (#1993) hit strategic_decisions_status_check, which
only permitted active/superseded/under_review. Phase D needs 'archived' as a
first-class status. This drops + re-adds the CHECK with 'archived' included.
Idempotent: no-op if 'archived' is already allowed. CLAUDE.md forbids
`supabase db push` against prod; use this direct psycopg-apply (decision-962).

Usage:
  python scripts/apply_archived_status.py            # dry-run (no writes)
  python scripts/apply_archived_status.py --apply    # commit the change
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

ALLOWED = ("active", "superseded", "under_review", "archived")

APPLY_SQL = """
alter table strategic_decisions drop constraint if exists strategic_decisions_status_check;
alter table strategic_decisions add constraint strategic_decisions_status_check
    check (status = any (array['active', 'superseded', 'under_review', 'archived']));
"""


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select pg_get_constraintdef(oid) from pg_constraint "
                "where conname = 'strategic_decisions_status_check'"
            )
            row = cur.fetchone()
            before = row[0] if row else "(absent)"
            print(f"status CHECK BEFORE: {before}")
            if row and "'archived'" in before:
                print("'archived' already allowed; nothing to do.")
                conn.rollback()
                return 0

            cur.execute(APPLY_SQL)

            cur.execute(
                "select pg_get_constraintdef(oid) from pg_constraint "
                "where conname = 'strategic_decisions_status_check'"
            )
            after = cur.fetchone()[0]
            print(f"status CHECK AFTER:  {after}")
            assert "'archived'" in after, "archived not in new constraint"

            if apply:
                conn.commit()
                print("\nAPPLIED + committed (status CHECK now allows 'archived').")
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
