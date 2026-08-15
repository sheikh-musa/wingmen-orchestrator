"""Apply migration 044 — operator_asks (the console's LIVE "Your asks" ledger).

CLAUDE.md forbids `supabase db push` against prod (decision-962). Use this direct
psycopg-apply instead (same pattern as scripts/apply_operator_backlog.py + the
PR #41/#42/#44 migration applies). It creates the table + indexes + RLS/grants
(idempotent). It does NOT seed — the ledger fills organically as the console
delegates asks (scripts/console_assign.py writes one link row per assign).

Usage:
  python scripts/apply_operator_asks.py            # dry-run (rolled back)
  python scripts/apply_operator_asks.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "044_operator_asks.sql"


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    sql = MIGRATION.read_text()

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.operator_asks')")
        print("operator_asks BEFORE:", cur.fetchone()[0] or "absent")

        cur.execute(sql)  # BEGIN/COMMIT inside the file run within this tx

        cur.execute("SELECT to_regclass('public.operator_asks')")
        if cur.fetchone()[0] is None:
            print("FAILED — table still absent", file=sys.stderr)
            conn.rollback()
            return 2

        # Prove the shape: the immutable-facts columns + the operator-close columns
        # exist and status is NOT a stored column (the whole point of this ledger).
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='operator_asks' "
            "ORDER BY ordinal_position")
        cols = [r[0] for r in cur.fetchall()]
        print("columns:", cols)
        assert "status" not in cols, "operator_asks must NOT store status (derive-live only)"
        for required in ("ask", "thread_id", "source_msg_id", "confirmed_at", "closed_at"):
            assert required in cols, f"missing column {required!r}"

        # Prove the read-only console role can SELECT (grant + RLS policy landed).
        cur.execute(
            "SELECT has_table_privilege('console_readonly','public.operator_asks','SELECT')")
        print("console_readonly can SELECT:", cur.fetchone()[0])
        # Prove it CANNOT write (the console derive-read must never mutate the ledger).
        cur.execute(
            "SELECT has_table_privilege('console_readonly','public.operator_asks','INSERT')")
        print("console_readonly can INSERT (must be False):", cur.fetchone()[0])

        cur.execute("SELECT count(*) FROM public.operator_asks")
        print("rows:", cur.fetchone()[0])

        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY RUN — rolled back. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
