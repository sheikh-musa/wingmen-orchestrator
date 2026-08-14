"""Apply migration 042 — pane_context (op#13050-B fresh pane-truth feed).

CLAUDE.md/decision-962 forbids `supabase db push` against prod. Use this direct
psycopg-apply. Idempotent (CREATE TABLE IF NOT EXISTS + DROP/CREATE POLICY).
Console-signed the schema add directly (ops-cache, not cai-gated) — bus 21515.

Usage:
  python scripts/apply_mig042_pane_context.py            # dry-run (rolled back)
  python scripts/apply_mig042_pane_context.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "042_pane_context.sql"


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    sql = MIGRATION.read_text()

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.pane_context')")
        print("pane_context BEFORE:", cur.fetchone()[0] or "absent")

        cur.execute(sql)  # BEGIN/COMMIT inside the file run within this tx

        cur.execute("SELECT to_regclass('public.pane_context')")
        if cur.fetchone()[0] is None:
            print("FAILED — table still absent", file=sys.stderr)
            conn.rollback()
            return 2

        # Prove the grants/RLS landed as designed.
        cur.execute("SELECT has_table_privilege('service_role','public.pane_context','INSERT')")
        print("service_role can INSERT:", cur.fetchone()[0])
        cur.execute("SELECT has_table_privilege('console_readonly','public.pane_context','SELECT')")
        print("console_readonly can SELECT:", cur.fetchone()[0])
        cur.execute("SELECT has_table_privilege('anon','public.pane_context','INSERT')")
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
