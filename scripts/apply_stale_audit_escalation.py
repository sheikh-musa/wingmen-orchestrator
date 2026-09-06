#!/usr/bin/env python3
"""Apply migrations/050_stale_audit_escalation.sql via DIRECT psycopg.

NEVER `supabase db push` (decision 962). Target: the SUBSTRATE coordination-plane DB.

CAI-RESP-988 §4 backstop + cc-quality's three PR #76 findings (#23757).

THE POST-CONDITION THAT MATTERS MOST HERE IS "IS IT SCHEDULED". `nervous_system/pipeline_clock.py`
is correct code with no launchd job -- never scheduled, not stopped -- and that is why
bug_pipeline_readiness read 10/10 green off one seed INSERT for 39 days. So this script asserts
the cron.job row EXISTS and is ACTIVE, and separately EXECUTES the escalator against a stale
fixture to prove it actually escalates. A backstop nobody verified is a board that lies.

Usage:  scripts/apply_stale_audit_escalation.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "050_stale_audit_escalation.sql"

PROBE_REF = "PROBE-050-STALE"


def _exercise_escalator(cur) -> None:
    """Prove the backstop FIRES on a stale audit, and does NOT fire on a fresh one."""
    cur.execute("SAVEPOINT probe_stale")
    try:
        cur.execute(
            "INSERT INTO strategic_decisions "
            "  (decision_ref, title, decision, reasoning, domain, decided_by, "
            "   challenge_status, challengeable_until, bypass_review, source, audit_tier) "
            "VALUES (%s, 'probe', 'probe', 'probe', 'operations', 'cai', "
            "        'challenge_window', now() + interval '1 day', true, 'musa_direct', 'FULL')",
            (PROBE_REF,),
        )
        # One stale (assigned 48h ago, SLA 24h) and one fresh — same call must separate them.
        cur.execute(
            "INSERT INTO decision_audits (decision_ref, auditor_agent, assigned_by, assigned_at) "
            "VALUES (%s, 'cc-quality', 'orch-console', now() - interval '48 hours')",
            (PROBE_REF,),
        )
        cur.execute(
            "INSERT INTO decision_audits (decision_ref, auditor_agent, assigned_by, assigned_at) "
            "VALUES (%s, 'cc-orchestrator', 'orch-console', now())",
            (PROBE_REF,),
        )

        cur.execute("SELECT decision_ref, auditor_agent, action FROM escalate_stale_decision_audits()")
        fired = [r for r in cur.fetchall() if r[0] == PROBE_REF]
        if len(fired) != 1 or fired[0][1] != "cc-quality":
            raise SystemExit(
                f"BACKSTOP FAILED: expected exactly the 48h-old cc-quality audit to escalate, "
                f"got {fired!r}. Rolling back."
            )

        # It must raise a bus row cai can actually see. An escalation nobody is told about is the
        # same silence it exists to break.
        cur.execute(
            "SELECT count(*) FROM agent_messages WHERE to_agent='cai' "
            "AND subject LIKE %s", (f"STALE AUDIT: {PROBE_REF}%",),
        )
        (msgs,) = cur.fetchone()
        if msgs != 1:
            raise SystemExit(f"BACKSTOP FAILED: expected 1 bus row to cai, got {msgs}. Rolling back.")

        # And it must NOT re-escalate on the next run (escalated_at), or a neglected audit spams
        # the bus daily until it is muted — which is how a real alert gets ignored.
        cur.execute("SELECT decision_ref FROM escalate_stale_decision_audits()")
        again = [r for r in cur.fetchall() if r[0] == PROBE_REF]
        if again:
            raise SystemExit(f"BACKSTOP FAILED: re-escalated on second run: {again!r}. Rolling back.")

        # It must never close anything. An escalation that could close is accepted_by_timeout
        # under a new name.
        cur.execute("SELECT challenge_status, audit_state FROM decision_audit_state WHERE decision_ref=%s",
                    (PROBE_REF,))
        status, state = cur.fetchone()
        if status != "challenge_window" or state != "AUDIT-STALE":
            raise SystemExit(
                f"BACKSTOP FAILED: probe reads ({status!r}, {state!r}), expected "
                "('challenge_window', 'AUDIT-STALE'). Rolling back."
            )
        print("  exercised: stale escalates once, fresh does not, bus row raised, nothing closed")
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT probe_stale")


def _exercise_fail_closed_guard(cur) -> None:
    """cc-quality F1: the guard must now REFUSE an unknown decision instead of permitting it."""
    cur.execute("SAVEPOINT probe_f1")
    raised = False
    try:
        cur.execute(
            "INSERT INTO decision_audits (decision_ref, auditor_agent, assigned_by) "
            "VALUES ('NO-SUCH-DECISION-050', 'cc-quality', 'orch-console')"
        )
    except Exception:
        raised = True
    cur.execute("ROLLBACK TO SAVEPOINT probe_f1")
    if not raised:
        raise SystemExit(
            "F1 FAILED: an audit was accepted for an unknown decision_ref. The guard is still "
            "failing OPEN on a missing decider. Rolling back."
        )
    print("  exercised: F1 fail-closed — unknown decision REFUSED, not silently permitted")


def main() -> int:
    load_dotenv(ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL not set", file=sys.stderr)
        return 2

    sql = SQL_PATH.read_text()
    if "--dry-run" in sys.argv:
        print(f"dry-run: would apply {SQL_PATH.name} ({len(sql)} bytes)")
        return 0

    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql)

            _exercise_escalator(cur)
            _exercise_fail_closed_guard(cur)

            # THE pipeline_clock POST-CONDITION: is it actually scheduled, and is it ON?
            cur.execute(
                "SELECT jobid, schedule, active FROM cron.job "
                "WHERE jobname = 'escalate-stale-decision-audits'"
            )
            job = cur.fetchone()
            if not job:
                raise SystemExit(
                    "post-condition FAILED: no cron.job row for escalate-stale-decision-audits. "
                    "An unscheduled backstop is pipeline_clock all over again. Rolling back."
                )
            if not job[2]:
                raise SystemExit(
                    f"post-condition FAILED: cron job {job[0]} exists but active=false. Rolling back."
                )
            print(f"  scheduled: cron jobid={job[0]} schedule={job[1]!r} active={job[2]}")

            # Label/boolean coherence, carried forward from 049 and re-asserted because the view
            # was rewritten. A rewritten view is a new view.
            cur.execute(
                "SELECT count(*) FROM decision_audit_state "
                "WHERE is_audit_closed <> (audit_state = 'AUDITED-ACCEPTED')"
            )
            (drift,) = cur.fetchone()
            if drift:
                raise SystemExit(f"post-condition FAILED: {drift} label/boolean drift. Rolling back.")

            cur.execute("SELECT count(*) FROM decision_audit_state WHERE audit_state IS NULL")
            (unlabelled,) = cur.fetchone()
            if unlabelled:
                raise SystemExit(f"post-condition FAILED: {unlabelled} NULL audit_state. Rolling back.")

            # Informational only — never asserted.
            cur.execute(
                "SELECT audit_state, count(*) FROM decision_audit_state GROUP BY 1 ORDER BY 2 DESC"
            )
            print("decision_audit_state:")
            for state, n in cur.fetchall():
                print(f"  {state:24} {n:5}")
            cur.execute(
                "SELECT count(*) FROM decision_audit_state WHERE audit_state = 'UNTIERED-CANDIDATE'"
            )
            (cand,) = cur.fetchone()
            print(f"  completeness-lint: {cand} open untiered decision(s) look like money/residency "
                  "— a floor, not a proof (the detector's recall is poor)")

        print("applied 050_stale_audit_escalation.sql")
        print("EXERCISED: backstop fires once + closes nothing; F1 guard fails closed; job scheduled+active")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
