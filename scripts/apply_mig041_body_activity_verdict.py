"""Apply migration 041 — body_activity_verdict (op#11774 G-b VPS-instance oracle).

CLAUDE.md/decision-962 forbids `supabase db push` against prod. Use this direct
psycopg-apply. Idempotent (CREATE TABLE IF NOT EXISTS + DROP/CREATE POLICY).
Console-signed the schema add directly (ops-cache, not cai-gated) — 18932.

Usage:
  python scripts/apply_mig041_body_activity_verdict.py            # dry-run (rolled back)
  python scripts/apply_mig041_body_activity_verdict.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "041_body_activity_verdict.sql"


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    sql = MIGRATION.read_text()

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.body_activity_verdict')")
        print("body_activity_verdict BEFORE:", cur.fetchone()[0] or "absent")

        cur.execute(sql)  # BEGIN/COMMIT inside the file run within this tx

        cur.execute("SELECT to_regclass('public.body_activity_verdict')")
        if cur.fetchone()[0] is None:
            print("FAILED — table still absent", file=sys.stderr)
            conn.rollback()
            return 2

        # Prove the grants/RLS landed as designed.
        cur.execute(
            "SELECT has_table_privilege('service_role','public.body_activity_verdict','INSERT')")
        print("service_role can INSERT:", cur.fetchone()[0])
        cur.execute(
            "SELECT has_table_privilege('console_readonly','public.body_activity_verdict','SELECT')")
        print("console_readonly can SELECT:", cur.fetchone()[0])
        cur.execute(
            "SELECT has_table_privilege('anon','public.body_activity_verdict','INSERT')")
        print("anon can INSERT (must be False):", cur.fetchone()[0])

        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY RUN — rolled back. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
