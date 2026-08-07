#!/usr/bin/env python3
"""Apply migrations/022_opportunities_ledger.sql to the substrate (direct
psycopg — decision-962: the supabase CLI shadow-diff path is forbidden against
prod). Idempotent: CREATE TABLE IF NOT EXISTS + guarded trigger/index.

Head of Revenue Phase 1 — the `opportunities` revenue ledger. Additive, no
enforcement, no behavior change."""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL / SUPABASE_DB_URL not set", file=sys.stderr)
        return 1
    sql = open(os.path.join(ROOT, "migrations", "022_opportunities_ledger.sql")).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
        cur.execute(
            "SELECT count(*), "
            "count(*) FILTER (WHERE stage IN ('won','paid','delivered')) "
            "FROM opportunities"
        )
        total, closed = cur.fetchone()
        print(f"opportunities table present — {total} rows ({closed} won/paid/delivered).")
    print("022_opportunities_ledger applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
