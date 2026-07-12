#!/usr/bin/env python3
"""Apply migrations/024_coordinator_panes.sql (direct psycopg, decision-962
safe). Idempotent — CREATE TABLE IF NOT EXISTS + ledger ON CONFLICT DO NOTHING."""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    sql = open(os.path.join(ROOT, "migrations", "024_coordinator_panes.sql")).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
    print("024_coordinator_panes applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
