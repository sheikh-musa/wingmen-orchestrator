#!/usr/bin/env python3
"""Apply migrations/018_finance_subscriptions.sql (direct psycopg, decision-962
safe). Idempotent (seeds ON CONFLICT DO NOTHING)."""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    sql = open(os.path.join(ROOT, "migrations", "018_finance_subscriptions.sql")).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cur.execute("SELECT * FROM finance_burn")
        cols = [d[0] for d in cur.description]
        print(dict(zip(cols, cur.fetchone())))
        conn.commit()
    print("018_finance_subscriptions applied.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
