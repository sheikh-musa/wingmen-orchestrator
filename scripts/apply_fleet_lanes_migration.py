"""Apply migrations/002_fleet_lanes.sql to project tscuymavysscrvoberrr.

Direct psycopg-apply (decision-962: never `supabase db push` to prod). Dry-run
default; --apply commits. Gated on operator sign-off of the fleet_lanes schema
(CAI-RESP-255 #4) — do not run --apply before then.

Usage:
  python scripts/apply_fleet_lanes_migration.py          # dry-run (rolled back)
  python scripts/apply_fleet_lanes_migration.py --apply   # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

SQL = pathlib.Path(__file__).resolve().parent.parent.joinpath(
    "migrations/002_fleet_lanes.sql").read_text()


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL/SUPABASE_DB_URL not set (project tscuymavysscrvoberrr)")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(SQL)
        cur.execute("select to_regclass('public.fleet_lanes')")
        print("fleet_lanes present after DDL:", cur.fetchone()[0])
        cur.execute("select lane, desired_state, launcher from fleet_lanes order by lane")
        print("seeded rows:", cur.fetchall())
        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
