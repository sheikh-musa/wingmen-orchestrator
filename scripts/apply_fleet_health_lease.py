#!/usr/bin/env python3
"""Apply migrations/023_fleet_health_lease.sql to the substrate (direct psycopg —
decision-962: the supabase CLI shadow-diff path is forbidden against prod).
Idempotent: CREATE IF NOT EXISTS + guarded seed + ON CONFLICT DO NOTHING.

Pre-req: cc-fleet-health must be registered (scripts/register_fleet_health.py) —
the seed's holder FK references agents(id). If the agent row is missing the table
is still created but the seed row is skipped (loudly reported)."""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL / SUPABASE_DB_URL not set in .env")
        return 1
    sql = open(os.path.join(ROOT, "migrations", "023_fleet_health_lease.sql")).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agents WHERE id='cc-fleet-health'")
        registered = cur.fetchone() is not None
        cur.execute(sql)
        cur.execute(
            "SELECT lease_key, holder, holder_host, renewed_at, ttl_seconds "
            "FROM fleet_health_lease")
        rows = cur.fetchall()
        conn.commit()
    for row in rows:
        print("fleet_health_lease:", row)
    if not registered:
        print("⚠️  agents.id='cc-fleet-health' MISSING — seed row skipped. Run "
              "`.venv/bin/python3 -m scripts.register_fleet_health` then re-apply.")
        return 1
    if not rows:
        print("⚠️  table created but no lease row present — investigate.")
        return 1
    print("023_fleet_health_lease applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
