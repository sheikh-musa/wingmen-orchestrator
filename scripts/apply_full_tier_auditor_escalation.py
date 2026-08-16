#!/usr/bin/env python3
"""Apply migration 059 by DIRECT psycopg in ONE managed transaction (never `supabase db push`).

Post-conditions asserted INSIDE the txn. Rolls back on any failure: a half-wired escalation is
worse than none, because the next reader takes the presence of a 059 as evidence it fires.
"""
import os, sys, psycopg2
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(HERE, ".env"))
SQL = os.path.join(HERE, "migrations", "059_full_tier_implies_an_auditor.sql")

def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(open(SQL).read())
        cur.execute("select to_regclass('public.decision_tier_escalations')")
        assert cur.fetchone()[0], "post-condition: dedupe table missing"
        cur.execute("select pg_get_functiondef('public.escalate_full_tier_without_auditor'::regproc)")
        d = cur.fetchone()[0]
        assert "audit_tier = 'FULL'" in d and "decision_tier_escalations" in d, \
            "post-condition: function body is not the expected predicate"
        cur.execute("select command, active from cron.job where jobid=10")
        cmd, active = cur.fetchone()
        assert "escalate_full_tier_without_auditor" in cmd, "post-condition: NOT wired to jobid 10"
        assert "escalate_stale_decision_audits" in cmd, "post-condition: the original call was LOST"
        assert active, "post-condition: jobid 10 is not active"
        # CALL IT. The first version of this script asserted only on the function's TEXT and the
        # cron wiring, both of which passed while the body threw UndefinedFunction on every
        # invocation (make_interval's `hours` is integer; the parameter is numeric). A migration
        # that creates an uncallable function is exactly the shape of bug this whole audit
        # mechanism exists to catch, and the check has to execute the thing.
        cur.execute("select * from escalate_full_tier_without_auditor(99999)")
        rows = cur.fetchall()
        assert rows == [], f"post-condition: a 99999h grace must escalate nothing, got {rows}"
        conn.commit(); print("APPLIED 059 — post-conditions held; wired to cron jobid 10")
        return 0
    except Exception as e:
        conn.rollback(); print(f"ROLLED BACK: {e}", file=sys.stderr); return 1
    finally:
        conn.close()

if __name__ == "__main__":
    sys.exit(main())
