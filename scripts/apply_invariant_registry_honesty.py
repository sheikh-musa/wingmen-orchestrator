#!/usr/bin/env python3
"""Apply migrations/047_invariant_registry_honesty.sql via DIRECT psycopg.

NEVER `supabase db push` (decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001): the CLI's shadow-diff
path re-applies historic CREATE OR REPLACE VIEW bodies from older migrations and silently strips
later arms. This is the orch's standard direct-apply pattern (PR #41/#42/#44).

Target: the SUBSTRATE coordination-plane DB (DATABASE_URL), NOT any client silo.

Additive and reversible: creates one view + two COMMENTs. Touches no data, no columns, no
constraints. Applied in ONE managed transaction; verifies AFTER apply and rolls back if the
post-condition fails, so a half-applied honesty fix can never be reported as done.

Usage:  scripts/apply_invariant_registry_honesty.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "047_invariant_registry_honesty.sql"


def main() -> int:
    load_dotenv(ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL not set", file=sys.stderr)
        return 2

    sql = SQL_PATH.read_text()
    if "--dry-run" in sys.argv:
        print(f"dry-run: would apply {SQL_PATH.name} ({len(sql)} bytes) to the substrate DB")
        return 0

    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql)

            # POST-CONDITION, checked inside the same transaction. The whole point of this
            # migration is that nothing reads as coverage when it has never been exercised, so
            # the check is exactly that: with no sink wired, EVERY row must read UNEXERCISED and
            # NO row may be is_exercised_fresh. If that is not true, the view is lying in the
            # direction this migration exists to prevent -- fail loudly rather than commit it.
            cur.execute(
                "SELECT count(*), "
                "count(*) FILTER (WHERE exercise_state = 'UNEXERCISED'), "
                "count(*) FILTER (WHERE is_exercised_fresh) "
                "FROM invariant_registry_state"
            )
            total, unexercised, fresh = cur.fetchone()
            print(f"invariant_registry_state: {total} rows, {unexercised} UNEXERCISED, {fresh} exercised-fresh")
            if fresh != 0 or unexercised != total:
                raise SystemExit(
                    f"post-condition FAILED: expected all {total} rows UNEXERCISED and 0 fresh "
                    f"(no sink is wired yet); got unexercised={unexercised} fresh={fresh}. "
                    "Rolling back — an honesty view that reports coverage is worse than none."
                )
        print("applied 047_invariant_registry_honesty.sql")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
