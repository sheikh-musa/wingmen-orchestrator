"""Apply migration 057 — pane_context.pane_k_at (never-blank lane-context fix,
Musa flag via Nazim #32472; design #32484/#32489).

CLAUDE.md/decision-962 forbids `supabase db push` against prod. Use this direct
psycopg-apply. Idempotent (ADD COLUMN IF NOT EXISTS + a NULL-only backfill). Additive
nullable timestamptz; inherits the table's RLS/grants unchanged (no policy/GRANT edit).
Console-signed schema add (ops-cache, not cai-gated) — same class as 042/043.

Unlike 043, the .sql carries NO BEGIN/COMMIT — this applier owns the transaction so its
dry-run TRULY rolls back (the 043 file COMMITs inside itself, defeating its own dry-run;
tolerable there only because ADD COLUMN IF NOT EXISTS is idempotent, but 057 also has a
backfill UPDATE that must not persist on a dry-run).

Usage:
  python scripts/apply_mig057_pane_context_pane_k_at.py            # dry-run (rolled back)
  python scripts/apply_mig057_pane_context_pane_k_at.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "057_pane_context_pane_k_at.sql"


def _col_exists(cur) -> bool:
    cur.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='pane_context' AND column_name='pane_k_at'"""
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
        print("pane_k_at column BEFORE:", "present" if _col_exists(cur) else "absent")

        cur.execute(sql)  # applier owns the tx; commit/rollback below

        if not _col_exists(cur):
            print("FAILED — pane_k_at column still absent after migration", file=sys.stderr)
            conn.rollback()
            return 3
        # Confirm type + nullability match the design (timestamptz, nullable).
        cur.execute(
            """SELECT data_type, is_nullable FROM information_schema.columns
               WHERE table_schema='public' AND table_name='pane_context' AND column_name='pane_k_at'"""
        )
        dtype, nullable = cur.fetchone()
        print(f"pane_k_at column AFTER: present (type={dtype}, nullable={nullable})")
        # Prove the backfill stamped every existing pane_k reading (no NULL-age readings).
        cur.execute("SELECT count(*) FROM public.pane_context WHERE pane_k IS NOT NULL AND pane_k_at IS NULL")
        orphan = cur.fetchone()[0]
        print(f"pane_k readings still missing pane_k_at (must be 0): {orphan}")
        if orphan:
            print("FAILED — backfill left pane_k readings with NULL pane_k_at", file=sys.stderr)
            conn.rollback()
            return 4
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
