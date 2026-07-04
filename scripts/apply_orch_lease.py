#!/usr/bin/env python3
"""Apply migrations/016_orch_lease.sql to the substrate (direct psycopg —
decision-962: the supabase CLI shadow-diff path is forbidden against prod).
Idempotent: CREATE IF NOT EXISTS + ON CONFLICT DO NOTHING."""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    sql = open(os.path.join(ROOT, "migrations", "016_orch_lease.sql")).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cur.execute("SELECT lease_key, holder, holder_host, acquired_at FROM orch_lease")
        for row in cur.fetchall():
            print("orch_lease:", row)
        conn.commit()
    print("016_orch_lease applied.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
