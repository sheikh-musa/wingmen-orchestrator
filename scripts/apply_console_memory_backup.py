#!/usr/bin/env python3
"""Apply migrations/017_console_memory_backup.sql (direct psycopg, decision-962
safe). Idempotent."""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    sql = open(os.path.join(ROOT, "migrations", "017_console_memory_backup.sql")).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
    print("017_console_memory_backup applied.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
