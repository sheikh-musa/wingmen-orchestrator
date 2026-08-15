"""Apply migration 043 — pane_context.pct (op#13186 cliff-truth column, additive over 042).

CLAUDE.md/decision-962 forbids `supabase db push` against prod. Use this direct
psycopg-apply. Idempotent (ADD COLUMN IF NOT EXISTS). Additive nullable smallint;
inherits 042's RLS/grants unchanged (no policy/GRANT edit). Console-signed schema
add (ops-cache, not cai-gated) — routed via fc-v53 ship (bus 21790/21801).

Usage:
  python scripts/apply_mig043_pane_context_pct.py            # dry-run (rolled back)
  python scripts/apply_mig043_pane_context_pct.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "043_pane_context_pct.sql"


def _col_exists(cur) -> bool:
    cur.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='pane_context' AND column_name='pct'"""
    )
    return cur.fetchone() is not None


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
        if cur.fetchone()[0] is None:
            print("FAILED — pane_context table absent (042 not applied?)", file=sys.stderr)
            return 2
        print("pct column BEFORE:", "present" if _col_exists(cur) else "absent")

        cur.execute(sql)  # BEGIN/COMMIT inside the file run within this tx

        if not _col_exists(cur):
            print("FAILED — pct column still absent after migration", file=sys.stderr)
            conn.rollback()
            return 3
        # Confirm type + nullability match the design (smallint, nullable).
        cur.execute(
            """SELECT data_type, is_nullable FROM information_schema.columns
               WHERE table_schema='public' AND table_name='pane_context' AND column_name='pct'"""
        )
        dtype, nullable = cur.fetchone()
        print(f"pct column AFTER: present (type={dtype}, nullable={nullable})")
        # Grants unchanged (inherited): prove console_readonly still SELECTs, anon still cannot INSERT.
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
