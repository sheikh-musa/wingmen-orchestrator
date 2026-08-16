#!/usr/bin/env python3
"""Apply migrations/054_anon_view_exposure_class_fix.sql via DIRECT psycopg.

cc-storefront #23776: 051 fixed three views; `boot_briefing` and seven others had the same shape.

NEVER `supabase db push` (decision 962 is specifically about boot_briefing).

THE DELIVERABLE IS THE CHECK, NOT THE LIST. `assert_no_owner_run_anon_views()` below fails if ANY
public view is owner-run AND readable by anon/authenticated. A hand-list of eight goes stale the
first time someone adds a view; this does not. Re-run this script any time to re-check the class
-- that is what makes it a control rather than an inventory, and inventories are what this fleet
spent the night discovering rot.

Usage:  scripts/apply_anon_view_exposure_class_fix.py [--dry-run] [--check-only]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "054_anon_view_exposure_class_fix.sql"

# The class query. A view is EXPOSED when it runs with the owner's privileges (so base-table RLS
# does not apply through it) AND a PostgREST role can read it.
CLASS_QUERY = """
SELECT c.relname,
       COALESCE(array_to_string(c.reloptions, ','), '') AS opts
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'v'
   AND n.nspname = 'public'
   AND COALESCE(array_to_string(c.reloptions, ','), '') NOT ILIKE '%security_invoker=on%'
   AND COALESCE(array_to_string(c.reloptions, ','), '') NOT ILIKE '%security_invoker=true%'
   AND EXISTS (
        SELECT 1 FROM information_schema.role_table_grants g
         WHERE g.table_schema = 'public'
           AND g.table_name = c.relname
           AND g.grantee IN ('anon', 'authenticated')
   )
 ORDER BY 1
"""


def _report_class(cur, label: str):
    cur.execute(CLASS_QUERY)
    rows = cur.fetchall()
    print(f"{label}: {len(rows)} owner-run view(s) readable by anon/authenticated")
    for name, opts in rows:
        print(f"  {name:34} reloptions={opts or '(none)'}")
    return rows


def main() -> int:
    load_dotenv(ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL not set", file=sys.stderr)
        return 2

    import psycopg2

    if "--check-only" in sys.argv:
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                rows = _report_class(cur, "CLASS CHECK")
            return 1 if rows else 0
        finally:
            conn.close()

    sql = SQL_PATH.read_text()
    if "--dry-run" in sys.argv:
        print(f"dry-run: would apply {SQL_PATH.name} ({len(sql)} bytes)")
        return 0

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            # BEFORE — measured as rows anon can actually read, not as configuration.
            cur.execute(CLASS_QUERY)
            before = [r[0] for r in cur.fetchall()]
            print("BEFORE — rows visible to anon:")
            for v in before:
                cur.execute("SAVEPOINT b")
                try:
                    cur.execute("SET LOCAL ROLE anon")
                    cur.execute(f"SELECT count(*) FROM {v}")
                    print(f"  {v:34} {cur.fetchone()[0]}")
                except Exception:
                    print(f"  {v:34} denied")
                cur.execute("ROLLBACK TO SAVEPOINT b")

            cur.execute(sql)

            # (1) THE CLASS CHECK. Not "did my eight land" — "is anything left".
            leftover = _report_class(cur, "AFTER (class check)")
            if leftover:
                raise SystemExit(
                    "post-condition FAILED: owner-run anon-readable views remain: "
                    f"{[r[0] for r in leftover]}. Rolling back."
                )

            # (2) EXECUTED, not inspected — the same discipline cc-quality used to prove the hole.
            for v in before:
                cur.execute("SAVEPOINT p")
                leaked = None
                try:
                    cur.execute("SET LOCAL ROLE anon")
                    cur.execute(f"SELECT count(*) FROM {v}")
                    leaked = cur.fetchone()[0]
                except Exception:
                    pass
                cur.execute("ROLLBACK TO SAVEPOINT p")
                if leaked is not None:
                    raise SystemExit(
                        f"post-condition FAILED: anon still reads {v} ({leaked} rows). Rolling back."
                    )
            print(f"VERIFIED: anon REFUSED on all {len(before)} (executed, not inspected)")

            # (3) The other direction. boot_briefing is the fleet's BOOT PATH — if this blinds it,
            #     every agent boots without its index and nothing obvious errors. Read it as the
            #     role agents actually use.
            cur.execute("SELECT current_user")
            who = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM boot_briefing")
            n = cur.fetchone()[0]
            if n == 0:
                raise SystemExit(
                    f"post-condition FAILED: boot_briefing reads 0 rows as {who}. The fix blinded "
                    "the boot path. Rolling back."
                )
            print(f"VERIFIED: boot_briefing still reads {n} rows as {who} (the agent boot path)")

        print("applied 054_anon_view_exposure_class_fix.sql")
        print("Re-run with --check-only any time to re-check the CLASS, not the list.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
