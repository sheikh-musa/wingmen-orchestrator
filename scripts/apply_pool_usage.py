"""Apply migration 036 — pool_usage (the console's weekly-% read path, op#9770).

CLAUDE.md forbids `supabase db push` against prod (decision-962). Use this direct
psycopg-apply instead. Idempotent: CREATE TABLE IF NOT EXISTS + DROP/CREATE POLICY,
so re-running is safe. No seed — the weekly_limit_monitor populates it on its next
poll.

Usage:
  python scripts/apply_pool_usage.py            # dry-run (rolled back)
  python scripts/apply_pool_usage.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "036_pool_usage.sql"


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    sql = MIGRATION.read_text()
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            # Prove the shape landed before we decide to commit.
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='pool_usage' ORDER BY ordinal_position")
            cols = [r[0] for r in cur.fetchall()]
            print("pool_usage columns:", cols)
        if apply:
            conn.commit()
            print("COMMITTED migration 036_pool_usage")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back) — pass --apply to commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
