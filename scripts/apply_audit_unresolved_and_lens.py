#!/usr/bin/env python3
"""Apply migrations/053_audit_unresolved_and_lens.sql via DIRECT psycopg.

cc-quality's F3 (top finding, HIGH, with a LIVE instance) and F5 from its rejection of
CAI-RESP-987/988 as built.

NEVER `supabase db push` (decision 962). Target: the SUBSTRATE coordination-plane DB.

THE EXERCISE REPLAYS THE LIVE INSTANCE. CAI-RESP-985 -- the money decision, tier FULL -- had a
completed `could_not_verify` and was invisible to the backstop: no timer, no escalation, no sink.
So the post-condition asserts that a could_not_verify past SLA now DOES escalate, that an
accepted one does NOT, and that resolving it stops the noise. Asserting the predicate by reading
it would be the same mistake the finding is about.

Usage:  scripts/apply_audit_unresolved_and_lens.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "053_audit_unresolved_and_lens.sql"
PROBE = "PROBE-053-UNRESOLVED"


def _exercise(cur) -> None:
    cur.execute("SAVEPOINT probe")
    try:
        cur.execute(
            "INSERT INTO strategic_decisions "
            "  (decision_ref, title, decision, reasoning, domain, decided_by, "
            "   challenge_status, challengeable_until, bypass_review, source, audit_tier) "
            "VALUES (%s,'probe','probe','probe','operations','cai','challenge_window',"
            "        now() + interval '1 day', true,'musa_direct','FULL')",
            (PROBE,),
        )
        # THE LIVE SHAPE: completed 48h ago with could_not_verify, never resolved.
        cur.execute(
            "INSERT INTO decision_audits "
            "  (decision_ref, auditor_agent, assigned_by, lens, verdict, checks_performed, "
            "   completed_at, assigned_at) "
            "VALUES (%s,'cc-quality','cai','governance-design-fidelity','could_not_verify',"
            "        %s, now() - interval '48 hours', now() - interval '72 hours')",
            (PROBE, "probe: replays cc-quality's live CAI-985 shape — completed, inconclusive, unresolved"),
        )
        # A control that must NOT fire: accepted, same age.
        cur.execute(
            "INSERT INTO decision_audits "
            "  (decision_ref, auditor_agent, assigned_by, lens, verdict, checks_performed, "
            "   completed_at, assigned_at) "
            "VALUES (%s,'cc-storefront','cai','implementation-correctness','accepted',"
            "        %s, now() - interval '48 hours', now() - interval '72 hours')",
            (PROBE, "probe control: an ACCEPTED audit of the same age must never escalate"),
        )

        cur.execute("SELECT decision_ref, auditor_agent, action FROM escalate_stale_decision_audits()")
        fired = [r for r in cur.fetchall() if r[0] == PROBE]
        if len(fired) != 1 or fired[0][1] != "cc-quality" or fired[0][2] != "escalated_unresolved":
            raise SystemExit(
                f"F3 FIX FAILED: expected exactly the unresolved could_not_verify to escalate, "
                f"got {fired!r}. That is the live CAI-985 hole, still open. Rolling back."
            )
        print("  exercised: unresolved could_not_verify NOW escalates; accepted control does NOT")

        # It must say WHICH kind, or cai cannot triage it from the subject line.
        cur.execute(
            "SELECT count(*) FROM agent_messages WHERE to_agent='cai' "
            "AND subject LIKE %s", (f"STALE AUDIT (UNRESOLVED COULD_NOT_VERIFY): {PROBE}%",),
        )
        (msgs,) = cur.fetchone()
        if msgs != 1:
            raise SystemExit(f"F3 FIX FAILED: expected 1 typed bus row, got {msgs}. Rolling back.")

        # Board must COUNT it as still needing somebody — n_open cannot see it.
        cur.execute(
            "SELECT n_open, n_unresolved, n_stale, lenses FROM decision_audit_state "
            "WHERE decision_ref=%s", (PROBE,),
        )
        n_open, n_unres, n_stale, lenses = cur.fetchone()
        if n_open != 0 or n_unres != 1 or n_stale != 1:
            raise SystemExit(
                f"F3 FIX FAILED: board reads n_open={n_open}, n_unresolved={n_unres}, "
                f"n_stale={n_stale}; expected 0/1/1. Rolling back."
            )
        if sorted(lenses or []) != ["governance-design-fidelity", "implementation-correctness"]:
            raise SystemExit(f"F5 FAILED: lenses={lenses!r}. Rolling back.")
        print(f"  exercised: board n_open=0 but n_unresolved=1 (the count n_open cannot see); lenses={lenses}")

        # RESOLVING it must stop the noise — and resolution is a SEPARATE act from the verdict.
        cur.execute(
            "UPDATE decision_audits SET resolved_at=now(), resolved_by='cai', "
            "       resolution_note='probe: acted on' "
            " WHERE decision_ref=%s AND auditor_agent='cc-quality'", (PROBE,),
        )
        cur.execute("SELECT n_unresolved, n_stale FROM decision_audit_state WHERE decision_ref=%s", (PROBE,))
        n_unres2, n_stale2 = cur.fetchone()
        if n_unres2 != 0 or n_stale2 != 0:
            raise SystemExit(
                f"RESOLUTION FAILED: after resolving, n_unresolved={n_unres2} n_stale={n_stale2}; "
                "expected 0/0. Rolling back."
            )
        print("  exercised: resolving it clears the item (resolution is a separate act from the verdict)")

        # A verdict must NOT be able to clear its own escalation.
        cur.execute(
            "SELECT decision_audit_unresolved('could_not_verify', now(), NULL), "
            "       decision_audit_unresolved('rejected', now(), NULL), "
            "       decision_audit_unresolved('accepted', now(), NULL), "
            "       decision_audit_unresolved(NULL, NULL, NULL)"
        )
        cnv, rej, acc, noverdict = cur.fetchone()
        if not (cnv and rej and noverdict) or acc:
            raise SystemExit(
                f"PREDICATE FAILED: cnv={cnv} rej={rej} acc={acc} none={noverdict}. Rolling back."
            )
        print("  exercised: unresolved predicate — cnv/rejected/no-verdict TRUE, accepted FALSE")
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT probe")


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
            _exercise(cur)

            cur.execute(
                "SELECT count(*) FROM decision_audit_state "
                "WHERE is_audit_closed <> (audit_state = 'AUDITED-ACCEPTED') OR audit_state IS NULL"
            )
            (drift,) = cur.fetchone()
            if drift:
                raise SystemExit(f"post-condition FAILED: {drift} drift/NULL rows. Rolling back.")

            # THE REAL ONE. Report what the fix now sees that it could not before -- CAI-985
            # specifically, since that is the instance cc-quality found sitting in the hole.
            cur.execute(
                "SELECT decision_ref, audit_state, n_open, n_unresolved, n_stale, n_escalated "
                "FROM decision_audit_state WHERE n_assigned > 0 ORDER BY 1"
            )
            print("live audit board after the fix:")
            for r in cur.fetchall():
                print(f"  {r[0]:16} {r[1]:22} open={r[2]} unresolved={r[3]} stale={r[4]} escalated={r[5]}")

        print("applied 053_audit_unresolved_and_lens.sql")
        print("EXERCISED: F3 — an inconclusive audit now escalates; F5 — lens recorded per auditor")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
