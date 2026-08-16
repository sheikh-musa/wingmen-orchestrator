#!/usr/bin/env python3
"""Apply migrations/049_assigned_decision_audit.sql via DIRECT psycopg.

NEVER `supabase db push` (decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001): the CLI's shadow-diff
path re-applies historic CREATE OR REPLACE VIEW bodies and silently strips later arms.

Target: the SUBSTRATE coordination-plane DB (DATABASE_URL), NOT any client silo.

WHY THIS SCRIPT EXECUTES THINGS INSTEAD OF ONLY READING THEM
------------------------------------------------------------
Migration 047 shipped a view whose EXERCISED state was UNREACHABLE: the live-measurer allowlist
held three tokens its author invented and never checked against the CHECK constraint, so no sink
could ever have turned a row green -- and it would have looked CORRECT FOREVER, because
UNEXERCISED was also the right answer that day. It was found by RUNNING a simulated sink, not by
reading the file for the fourth time.

So this script does not merely assert facts about today's data. It EXERCISES the two paths that
this migration exists to provide, inside a SAVEPOINT that is rolled back:

  * the CLOSE path actually reaches `accepted_by_audit` on a real (temporary) row;
  * the AUDITOR-!=-DECIDER trigger actually RAISES when handed a self-audit.

A control that has never executed is not satisfied (CAI-978). That includes this one.

POST-CONDITIONS assert the view's PERMANENT invariants, never a transient fact about today's
data -- asserting "0 audits exist" would be true today and become a rollback landmine the moment
the first audit lands (cc-quality's F2 on PR #74).

Usage:  scripts/apply_assigned_decision_audit.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "049_assigned_decision_audit.sql"

# A temporary ref used only inside a rolled-back SAVEPOINT. is_test=false on purpose: the board
# view filters test rows out, so a test row could not exercise the close path at all -- and an
# exercise that cannot touch the real path proves nothing.
PROBE_REF = "PROBE-049-REACHABILITY"


def _exercise_close_path(cur) -> None:
    """Prove accepted_by_audit is REACHABLE. Rolled back; nothing survives."""
    cur.execute("SAVEPOINT probe_close")
    try:
        cur.execute(
            "INSERT INTO strategic_decisions "
            "  (decision_ref, title, decision, reasoning, domain, decided_by, "
            "   challenge_status, challengeable_until, bypass_review, source) "
            "VALUES (%s, 'probe', 'probe', 'probe', 'operations', 'cai', "
            "        'challenge_window', now() + interval '1 day', true, 'musa_direct')",
            (PROBE_REF,),
        )
        # A different lane from the decider, so the trigger permits it.
        cur.execute(
            "INSERT INTO decision_audits "
            "  (decision_ref, auditor_agent, assigned_by, verdict, checks_performed, completed_at) "
            "VALUES (%s, 'cc-quality', 'orch-console', 'accepted', %s, now())",
            (PROBE_REF, "probe: read the migration at source and re-derived the close path"),
        )
        cur.execute("SELECT close_decision_by_audit(%s, 'orch-console')", (PROBE_REF,))
        (result,) = cur.fetchone()
        if result != "closed":
            raise SystemExit(
                f"REACHABILITY FAILED: close_decision_by_audit returned {result!r}, not 'closed'. "
                "Rolling back — the same class of defect as 047's unreachable EXERCISED."
            )

        cur.execute(
            "SELECT challenge_status, audit_state, is_audit_closed "
            "FROM decision_audit_state WHERE decision_ref = %s",
            (PROBE_REF,),
        )
        row = cur.fetchone()
        if row != ("accepted_by_audit", "AUDITED-ACCEPTED", True):
            raise SystemExit(
                f"REACHABILITY FAILED: probe row reads {row!r}, expected "
                "('accepted_by_audit', 'AUDITED-ACCEPTED', True). Rolling back."
            )

        # could_not_verify must BLOCK the close, not round to a pass. This is the arm the whole
        # mechanism turns on, so it is executed rather than trusted.
        cur.execute(
            "UPDATE decision_audits SET verdict='could_not_verify' WHERE decision_ref=%s",
            (PROBE_REF,),
        )
        cur.execute("SAVEPOINT probe_cnv")
        blocked = False
        try:
            cur.execute("SELECT close_decision_by_audit(%s, 'orch-console')", (PROBE_REF,))
        except Exception:
            blocked = True
            cur.execute("ROLLBACK TO SAVEPOINT probe_cnv")
        else:
            cur.execute("ROLLBACK TO SAVEPOINT probe_cnv")
        if not blocked:
            raise SystemExit(
                "REACHABILITY FAILED: could_not_verify did NOT block the close. That is the one "
                "outcome that must never round to acceptance. Rolling back."
            )
        print("  exercised: close path reaches accepted_by_audit; could_not_verify BLOCKS it")
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT probe_close")


def _exercise_self_audit_guard(cur) -> None:
    """Prove auditor!=decider is enforced IN CODE, not by convention (criterion 3)."""
    cur.execute("SAVEPOINT probe_self")
    try:
        cur.execute(
            "INSERT INTO strategic_decisions "
            "  (decision_ref, title, decision, reasoning, domain, decided_by, "
            "   challenge_status, bypass_review, source) "
            "VALUES (%s, 'probe', 'probe', 'probe', 'operations', 'cc-orchestrator', "
            "        'unchallenged', true, 'musa_direct')",
            (PROBE_REF + "-SELF",),
        )
        for auditor in ("cc-orchestrator", "cc-orchestrator-1", "orch-console"):
            cur.execute("SAVEPOINT probe_self_one")
            raised = False
            try:
                cur.execute(
                    "INSERT INTO decision_audits (decision_ref, auditor_agent, assigned_by) "
                    "VALUES (%s, %s, 'cai')",
                    (PROBE_REF + "-SELF", auditor),
                )
            except Exception:
                raised = True
            cur.execute("ROLLBACK TO SAVEPOINT probe_self_one")
            if not raised:
                raise SystemExit(
                    f"GUARD FAILED: auditor {auditor!r} was accepted on a decision decided by "
                    "'cc-orchestrator'. That is PR #75's suffix bypass in a new place. Rolling back."
                )
        # And the guard must still PERMIT a genuinely independent auditor, or it is not a guard,
        # it is a wall (047's unreachable-EXERCISED failure, opposite sign).
        cur.execute(
            "INSERT INTO decision_audits (decision_ref, auditor_agent, assigned_by) "
            "VALUES (%s, 'cc-quality', 'cai')",
            (PROBE_REF + "-SELF",),
        )
        print("  exercised: self-audit REFUSED for cc-orchestrator/-1/orch-console; cc-quality permitted")
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT probe_self")


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

            # ---- EXERCISE (rolled back) -------------------------------------------------
            _exercise_close_path(cur)
            _exercise_self_audit_guard(cur)

            # ---- PERMANENT INVARIANTS ---------------------------------------------------

            # (1) Label and boolean cannot drift apart. Single-sourced in the view; asserted at
            #     every apply so a future edit to one arm alone cannot ship (047 F1).
            cur.execute(
                "SELECT count(*) FROM decision_audit_state "
                "WHERE is_audit_closed <> (audit_state = 'AUDITED-ACCEPTED')"
            )
            (drift,) = cur.fetchone()
            if drift:
                raise SystemExit(
                    f"post-condition FAILED: {drift} row(s) where is_audit_closed disagrees with "
                    "audit_state='AUDITED-ACCEPTED'. Rolling back — boolean and label drifted."
                )

            # (2) No NULL bucket. An unlabelled row is invisible to whoever is deciding what still
            #     needs attention — 047's F5.
            cur.execute("SELECT count(*) FROM decision_audit_state WHERE audit_state IS NULL")
            (unlabelled,) = cur.fetchone()
            if unlabelled:
                raise SystemExit(
                    f"post-condition FAILED: {unlabelled} row(s) with NULL audit_state. Rolling back."
                )

            # (3) accepted_by_audit is not reachable without an accepted audit. The status is the
            #     claim; the audit row is the evidence. If these can come apart, the status means
            #     nothing.
            cur.execute(
                "SELECT count(*) FROM decision_audit_state "
                "WHERE challenge_status = 'accepted_by_audit' AND n_accepted = 0"
            )
            (hollow,) = cur.fetchone()
            if hollow:
                raise SystemExit(
                    f"post-condition FAILED: {hollow} row(s) closed accepted_by_audit with NO "
                    "accepted audit behind them. Rolling back."
                )

            # (4) The board and the enforcer consume ONE definition, so they cannot disagree about
            #     what owes an audit. Asserted rather than assumed, because a board that says
            #     AUDIT-OWED while the enforcer closes the row anyway is worse than neither.
            cur.execute(
                "SELECT count(*) FROM decision_audit_state "
                "WHERE audit_state IN ('AUDIT-OWED', 'AUDIT-IN-FLIGHT') AND NOT audit_required"
            )
            (disagree,) = cur.fetchone()
            if disagree:
                raise SystemExit(
                    f"post-condition FAILED: {disagree} row(s) the board calls audit-owed but the "
                    "enforcer would still close on timeout. Rolling back."
                )

            # ---- Informational only — NEVER asserted (that is what F2 was) ----------------
            cur.execute(
                "SELECT audit_state, count(*) FROM decision_audit_state GROUP BY 1 ORDER BY 2 DESC"
            )
            print("decision_audit_state:")
            for state, n in cur.fetchall():
                print(f"  {state:22} {n:5}")
            cur.execute(
                "SELECT count(*) FILTER (WHERE untiered), count(*) FROM decision_audit_state"
            )
            untiered, total = cur.fetchone()
            print(f"  untiered: {untiered}/{total} — watch this; if it stays at 100% the tier "
                  "field is decorative and tiering must move into decision CREATION")

        print("applied 049_assigned_decision_audit.sql")
        print("EXERCISED: close path reachable; could_not_verify blocks; self-audit refused")
        print("post-conditions OK: label==boolean; no NULL state; no hollow close; board==enforcer")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
