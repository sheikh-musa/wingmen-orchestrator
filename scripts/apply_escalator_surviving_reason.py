#!/usr/bin/env python3
"""Apply migration 058 by DIRECT psycopg in ONE managed transaction.

NEVER `supabase db push` (decision 962): its shadow-diff path re-applies historic
CREATE OR REPLACE statements from older migrations and silently strips later arms.

Post-conditions asserted INSIDE the transaction, rolled back if any fails — a function that
still carries the refuted argument is worse than not having touched it, because the next reader
takes the presence of a 058 as evidence it was fixed.
"""
import os, sys, psycopg2
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(HERE, ".env"))
SQL = os.path.join(HERE, "migrations", "058_escalator_carries_the_surviving_reason.sql")
REFUTED = "wearing a better coat"

def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL missing", file=sys.stderr); return 2
    body = open(SQL).read()
    conn = psycopg2.connect(dsn); conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("select pg_get_functiondef('public.escalate_stale_decision_audits'::regproc)")
        before = cur.fetchone()[0]
        assert REFUTED in before, "pre-condition: the refuted sentence should be present BEFORE the fix"
        cur.execute(body)
        cur.execute("select pg_get_functiondef('public.escalate_stale_decision_audits'::regproc)")
        after = cur.fetchone()[0]
        assert REFUTED not in after.replace(
            "auto-close resembles accepted_by_timeout", ""), \
            "post-condition FAILED: the refuted argument is still asserted"
        assert "DELETE THE LAST MOMENT ANYONE READS THE AUDIT" in after, \
            "post-condition FAILED: the surviving reason is not present"
        assert "checks_performed" in after, \
            "post-condition FAILED: the close prompt does not point at checks_performed"
        assert "string_agg(da.auditor_agent" in after, \
            "post-condition FAILED: auditors+lenses are not carried"
        # paths 1+2 must be untouched
        for marker in ("CAI-RESP-988 §4 backstop.", "escalated_not_started", "escalated_unresolved"):
            assert marker in after, f"post-condition FAILED: path 1/2 marker lost: {marker}"
        conn.commit(); print("APPLIED 058 — all post-conditions held")
        return 0
    except Exception as e:
        conn.rollback(); print(f"ROLLED BACK: {e}", file=sys.stderr); return 1
    finally:
        conn.close()

if __name__ == "__main__":
    sys.exit(main())
