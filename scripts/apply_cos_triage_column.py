#!/usr/bin/env python3
"""Apply migrations/021_operator_messages_cos_triage.sql to the substrate
(direct psycopg — decision-962: the supabase CLI shadow-diff path is forbidden
against prod). Idempotent + additive: ADD COLUMN IF NOT EXISTS (nullable jsonb).

Run:  .venv/bin/python3 scripts/apply_cos_triage_column.py
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
                            "021_operator_messages_cos_triage.sql")).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name='operator_messages' AND column_name='cos_triage'")
        print("operator_messages.cos_triage:", cur.fetchone())
    print("021_operator_messages_cos_triage applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
