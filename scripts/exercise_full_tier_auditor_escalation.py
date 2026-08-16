#!/usr/bin/env python3
"""Exercise migration 059's escalation against the APPLIED function, then ROLL BACK.

Why this file exists rather than a note saying "I tested it": applying 059 the first time reported
"post-conditions held" while the function threw UndefinedFunction on EVERY call. The post-conditions
read the function's TEXT and the cron wiring and never invoked it. This runs the real thing.

Everything happens in one transaction that is always rolled back, so it is safe against live
governance data — it removes an auditor row to manufacture the FULL-with-no-auditor state, watches
the escalation fire, then unwinds. Re-verifies on a separate connection that nothing survived.
"""
import os, sys, psycopg2, psycopg2.extras
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(HERE, ".env"))
REF = os.environ.get("EXERCISE_REF", "CAI-RESP-1001")

def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    failures = []
    def check(label, cond):
        print(("  PASS  " if cond else "  FAIL  ") + label)
        if not cond: failures.append(label)
    try:
        cur.execute("select * from escalate_full_tier_without_auditor()")
        check("clean board, default grace -> nothing", len(cur.fetchall()) == 0)

        cur.execute("delete from decision_audits where decision_ref=%s", (REF,))
        cur.execute("select * from escalate_full_tier_without_auditor()")
        check("default 2h grace suppresses a freshly-decided ruling", len(cur.fetchall()) == 0)

        cur.execute("select * from escalate_full_tier_without_auditor(0)")
        fired = cur.fetchall()
        check("grace=0 fires for the unassigned FULL ruling",
              any(r["decision_ref"] == REF and r["action"] == "escalated_full_no_auditor" for r in fired))
        cur.execute("select count(*) c from agent_messages where subject like 'FULL TIER, NO AUDITOR%%'")
        check("a bus row was actually written", cur.fetchone()["c"] == len(fired))
        cur.execute("select count(*) c from decision_tier_escalations")
        check("a dedupe row was actually written", cur.fetchone()["c"] == len(fired))
        cur.execute("select body from agent_messages where subject like 'FULL TIER, NO AUDITOR%%' order by id desc limit 1")
        body = cur.fetchone()["body"]
        check("body cites the ruling and states the fix", "CAI-RESP-1001 §2" in body and "INLINE" in body)

        cur.execute("select * from escalate_full_tier_without_auditor(0)")
        check("second run DEDUPES (hourly cron must not storm)", len(cur.fetchall()) == 0)

        cur.execute("delete from decision_tier_escalations")
        cur.execute("select * from escalate_full_tier_without_auditor(9999)")
        check("a huge grace gates everything", len(cur.fetchall()) == 0)
        cur.execute("select * from escalate_full_tier_without_auditor(0.0001)")
        check("FRACTIONAL grace works (numeric, not make_interval's integer hours)",
              len(cur.fetchall()) >= 1)
    finally:
        conn.rollback(); conn.close()

    v = psycopg2.connect(os.environ["DATABASE_URL"]); v.set_session(readonly=True)
    k = v.cursor()
    k.execute("select (select count(*) from decision_audits where decision_ref=%s),"
              "(select count(*) from agent_messages where subject like 'FULL TIER, NO AUDITOR%%'),"
              "(select count(*) from decision_tier_escalations)", (REF,))
    got = k.fetchone(); v.close()
    print(f"  {'PASS' if got == (1,0,0) else 'FAIL'}  rollback left no trace: {got} (expect (1, 0, 0))")
    if got != (1, 0, 0): failures.append("rollback left a trace")

    print(("\nFAILURES: " + "; ".join(failures)) if failures else "\nALL PASSED")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
