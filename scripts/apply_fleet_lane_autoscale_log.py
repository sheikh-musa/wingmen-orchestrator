#!/usr/bin/env python3
"""Apply migrations/062_fleet_lane_autoscale_log.sql to the substrate (direct psycopg —
decision-962: the supabase CLI shadow-diff path is forbidden against prod).

INERT-phase artefact for the irsyad-autoscaler (docs/irsyad-autoscaler-design-v1.md §6).
Idempotent: CREATE TABLE / INDEX IF NOT EXISTS + a guarded console-read grant/policy.

DO NOT run this yet. Per CLAUDE.md the hub applies substrate migrations after review;
this script is delivered UNAPPLIED alongside the migration for the hub to run post-review."""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

_MIGRATION = "062_fleet_lane_autoscale_log.sql"


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL / SUPABASE_DB_URL not set in .env")
        return 1
    sql = open(os.path.join(ROOT, "migrations", _MIGRATION)).read()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname='console_readonly'")
        console_role = cur.fetchone() is not None
        cur.execute(sql)
        # Verify the table + the console read policy (when the role exists) landed.
        cur.execute("SELECT to_regclass('public.fleet_lane_autoscale_log')")
        table = cur.fetchone()[0]
        cur.execute(
            "SELECT policyname FROM pg_policies "
            "WHERE tablename='fleet_lane_autoscale_log' ORDER BY policyname")
        policies = [r[0] for r in cur.fetchall()]
        conn.commit()
    print(f"table: {table}")
    print(f"policies: {policies}")
    if table is None:
        print("⚠️  table not present after apply — investigate.")
        return 1
    if not console_role:
        print("⚠️  console_readonly role ABSENT — table created but the wet-prove console "
              "read grant/policy was SKIPPED. Apply migration 004_console_readonly first, "
              "then re-run to add the console SELECT.")
        return 1
    if "sel_console_fleet_lane_autoscale_log" not in policies:
        print("⚠️  console read policy missing — investigate.")
        return 1
    print(f"{_MIGRATION} applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
