#!/usr/bin/env python3
"""Apply migrations/020_operator_messages_sender_identity.sql to the substrate
(direct psycopg — decision-962: the supabase CLI shadow-diff path is forbidden
against prod). Idempotent: ADD COLUMN IF NOT EXISTS.

Run:  .venv/bin/python3 scripts/apply_operator_sender_identity.py
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    sql = open(os.path.join(ROOT, "migrations",
                            "020_operator_messages_sender_identity.sql")).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='operator_messages' "
            "AND column_name IN ('from_user_id','from_username','from_name') "
            "ORDER BY column_name")
        print("operator_messages sender cols:", [r[0] for r in cur.fetchall()])
    print("020_operator_messages_sender_identity applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
