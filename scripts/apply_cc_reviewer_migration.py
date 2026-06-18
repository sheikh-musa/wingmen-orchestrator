"""Apply migrations/003_cc_reviewer.sql to the orchestrator substrate (tscuymavysscrvoberrr).

Direct psycopg-apply (decision-962: never `supabase db push` to prod). Dry-run
default (rolled back); --apply commits. Per CAI-RESP-257 the dry-run output is
posted to cai on thread ef567cb4 for review BEFORE --apply.

Usage:
  python scripts/apply_cc_reviewer_migration.py           # dry-run (rolled back)
  python scripts/apply_cc_reviewer_migration.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

SQL = pathlib.Path(__file__).resolve().parent.parent.joinpath(
    "migrations/003_cc_reviewer.sql").read_text()


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL/SUPABASE_DB_URL not set")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(SQL)
        # verification
        cur.execute("SELECT id, repo_scope, status FROM agents WHERE id='cc-reviewer'")
        print("agents.cc-reviewer:", cur.fetchone())
        cur.execute("SELECT lane, base_agent_id, launcher, desired_state, worktree_path FROM fleet_lanes WHERE lane='reviewer'")
        print("fleet_lanes.reviewer:", cur.fetchone())
        cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='fleet_lanes_desired_state_check'")
        print("desired_state CHECK:", cur.fetchone()[0])
        cur.execute("SELECT name, mandatory, applies_when FROM review_dimensions ORDER BY name")
        print("review_dimensions:", cur.fetchall())
        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
