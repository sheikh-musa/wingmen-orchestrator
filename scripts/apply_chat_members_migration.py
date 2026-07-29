"""Apply migrations/025_chat_members.sql to the orchestrator SUBSTRATE DB.

Direct psycopg-apply (decision-962: never `supabase db push`). Idempotent
(CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING seed) so re-running is
safe. Dry-run by default (rolled back); --apply commits.

Usage:
  python scripts/apply_chat_members_migration.py           # dry-run (rolled back)
  python scripts/apply_chat_members_migration.py --apply    # commit

Leave --apply to the hub (reviewer). This targets the substrate DB via
DATABASE_URL / SUPABASE_DB_URL — NOT the ihsanos production silo.
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL = ROOT.joinpath("migrations/025_chat_members.sql").read_text()


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv(ROOT.joinpath(".env"))
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set (substrate DB)")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(SQL)
        cur.execute("select to_regclass('public.chat_members')")
        print("chat_members present after DDL:", cur.fetchone()[0])
        cur.execute("select count(*) from chat_members")
        print("chat_members row count:", cur.fetchone()[0])
        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
