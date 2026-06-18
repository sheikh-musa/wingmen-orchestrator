"""Apply migrations/001_reel_inbox.sql to project tscuymavysscrvoberrr.

Direct psycopg-apply (decision-962: never `supabase db push` to prod). Dry-run
default; --apply commits. Gated on REEL_INBOX_DB_URL handoff (operator) +
challenge-window close per CAI-RESP-216/218 — do not run --apply before then.

Usage:
  python scripts/apply_reel_inbox_migration.py          # dry-run (rolled back)
  python scripts/apply_reel_inbox_migration.py --apply   # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

SQL = pathlib.Path(__file__).resolve().parent.parent.joinpath(
    "migrations/001_reel_inbox.sql").read_text()


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("REEL_INBOX_DB_URL")
    if not dsn:
        raise SystemExit("REEL_INBOX_DB_URL not set (project tscuymavysscrvoberrr)")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(SQL)
        cur.execute("select to_regclass('public.reel_inbox')")
        print("reel_inbox present after DDL:", cur.fetchone()[0])
        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
