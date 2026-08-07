"""Apply migration 033 — agent_status auth attribution (host / auth_account / auth_fp).

CLAUDE.md forbids `supabase db push` against prod (decision-962: the CLI's shadow-diff
re-applies historic CREATE OR REPLACE VIEW bodies and silently strips later arms from
boot_briefing). Use this direct psycopg-apply instead.

Purely additive: three nullable text columns + comments. Existing writers are unaffected.

Usage:
  python scripts/apply_agent_status_auth_attribution.py            # dry-run (rolled back)
  python scripts/apply_agent_status_auth_attribution.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

MIGRATION = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "033_agent_status_auth_attribution.sql"
EXPECTED = ("host", "auth_account", "auth_fp")


def main() -> int:
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    sql = MIGRATION.read_text()

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='agent_status' AND column_name = ANY(%s)", (list(EXPECTED),))
        before = {r[0] for r in cur.fetchall()}
        print("columns present BEFORE:", sorted(before) or "none")

        cur.execute(sql)

        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='agent_status' AND column_name = ANY(%s)", (list(EXPECTED),))
        after = {r[0] for r in cur.fetchall()}
        print("columns present AFTER :", sorted(after))
        missing = set(EXPECTED) - after
        if missing:
            print("FAILED — still missing:", sorted(missing), file=sys.stderr)
            conn.rollback()
            return 2

        # Prove the identity trigger still guards writes (this migration must not weaken it).
        cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid='agent_status'::regclass "
                    "AND NOT tgisinternal")
        print("triggers still on agent_status:", [r[0] for r in cur.fetchall()])

        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY RUN — rolled back. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
