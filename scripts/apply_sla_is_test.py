"""SUBSTRATE-COHERENCE-001 B3: inbox_sla_violations excludes is_test (psycopg-apply).

A test that pollutes the real inbox is a defect. The SLA view drives both the
boot_briefing inbox_sla_violation arm and operator alarms; test traffic must
never raise an SLA violation. The single fix point is the msg_with_age CTE
WHERE clause — both UNION arms read from it, so excluding is_test there covers
the whole view.

Reconstructs from the LIVE definition (pg_get_viewdef) via a targeted re.subn
on the CTE WHERE, preserving the rest verbatim. CLAUDE.md forbids
`supabase db push` to prod (decision-962 arm-stripping hazard); use this direct
psycopg-apply.

Usage:
  python scripts/apply_sla_is_test.py            # dry-run (rolled back)
  python scripts/apply_sla_is_test.py --apply    # commit
"""
from __future__ import annotations

import os
import re
import sys

import psycopg
from dotenv import load_dotenv

# Original precedence is: read_at IS NULL OR (requires_response AND responded_at
# IS NULL). We wrap that whole predicate and AND the is_test exclusion across it.
WHERE_RE = re.compile(
    r"WHERE am\.read_at IS NULL OR am\.requires_response = true "
    r"AND am\.responded_at IS NULL"
)
WHERE_NEW = (
    "WHERE (am.read_at IS NULL OR am.requires_response = true "
    "AND am.responded_at IS NULL) AND am.is_test IS NOT TRUE"
)


def transform(viewdef: str) -> str:
    new_def, n = WHERE_RE.subn(WHERE_NEW, viewdef)
    assert n == 1, f"msg_with_age WHERE anchor matched {n} times (expected 1)"
    return new_def


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select pg_get_viewdef('inbox_sla_violations'::regclass, true)"
            )
            viewdef = cur.fetchone()[0]
            new_def = transform(viewdef)
            cur.execute(
                f"CREATE OR REPLACE VIEW inbox_sla_violations AS {new_def}"
            )

            # Verify: no is_test=true row can surface as a violation.
            cur.execute("""
                select count(*) from inbox_sla_violations v
                join agent_messages am on am.id = v.message_id
                where am.is_test is true
            """)
            leaked = cur.fetchone()[0]
            cur.execute("select count(*) from inbox_sla_violations")
            total = cur.fetchone()[0]
            print(f"inbox_sla_violations rows: {total}  is_test leaks: {leaked}")

            if apply and leaked == 0:
                conn.commit()
                print("\nAPPLIED + committed.")
            elif apply:
                conn.rollback()
                print(f"\nABORTED: {leaked} is_test rows still leak; not committing.")
                return 1
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
