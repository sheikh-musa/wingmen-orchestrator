#!/usr/bin/env python3
"""Apply migrations/052_audit_builder_independence.sql via DIRECT psycopg.

CAI-RESP-989/990: the audit guard knew who DECIDED and not who BUILT.

NEVER `supabase db push` (decision 962). Target: the SUBSTRATE coordination-plane DB.

THE EXERCISE IS THE POINT, and it is not hypothetical: this replays THE ACTUAL FAILURE. cai
appointed the hub as second auditor of CAI-987, orch-console argued for it, and the trigger
ACCEPTED it. So the post-condition asserts that the identical assignment is now REFUSED — and,
just as importantly, that a genuinely independent auditor is still PERMITTED. A guard that
refuses everything is 047's unreachable-EXERCISED with the opposite sign.

Usage:  scripts/apply_audit_builder_independence.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "052_audit_builder_independence.sql"

PROBE = "PROBE-052-BUILDER"


def _exercise(cur) -> None:
    cur.execute("SAVEPOINT probe")
    try:
        cur.execute(
            "INSERT INTO strategic_decisions "
            "  (decision_ref, title, decision, reasoning, domain, decided_by, "
            "   challenge_status, bypass_review, source) "
            "VALUES (%s,'probe','probe','probe','operations','cai','unchallenged',true,'musa_direct')",
            (PROBE,),
        )

        # 1. THE REAL FAILURE, REPLAYED: builder=orch-console (the assigner), auditor=the hub.
        #    Before 052 this was ACCEPTED by the trigger and by all three of us.
        for auditor in ("cc-orchestrator", "cc-orchestrator-1", "orch-console"):
            cur.execute("SAVEPOINT one")
            blocked = False
            try:
                cur.execute(
                    "INSERT INTO decision_audits (decision_ref, auditor_agent, assigned_by) "
                    "VALUES (%s, %s, 'orch-console')",
                    (PROBE, auditor),
                )
            except Exception:
                blocked = True
            cur.execute("ROLLBACK TO SAVEPOINT one")
            if not blocked:
                raise SystemExit(
                    f"GUARD FAILED: auditor {auditor!r} accepted on a decision built by "
                    "'orch-console'. That is the exact CAI-988 failure, still open. Rolling back."
                )
        print("  exercised: builder-lane auditors REFUSED (cc-orchestrator/-1/orch-console)")

        # 2. AND IT MUST STILL PERMIT a genuinely independent body, or it is a wall, not a guard.
        cur.execute(
            "INSERT INTO decision_audits (decision_ref, auditor_agent, assigned_by) "
            "VALUES (%s, 'cc-storefront', 'orch-console') RETURNING id",
            (PROBE,),
        )
        if not cur.fetchone():
            raise SystemExit("GUARD FAILED: cc-storefront was refused. Rolling back.")
        print("  exercised: independent auditor (cc-storefront) still PERMITTED")

        # 3. THE OVERRIDE WORKS AND TIGHTENS: with built_by set to a DIFFERENT lane, an auditor in
        #    THAT lane must now be refused even though the assigner is unrelated.
        cur.execute("UPDATE strategic_decisions SET built_by='cc-shipforge' WHERE decision_ref=%s", (PROBE,))
        cur.execute("SAVEPOINT ov")
        blocked = False
        try:
            cur.execute(
                "INSERT INTO decision_audits (decision_ref, auditor_agent, assigned_by) "
                "VALUES (%s, 'cc-shipforge-1', 'cc-caai')",
                (PROBE,),
            )
        except Exception:
            blocked = True
        cur.execute("ROLLBACK TO SAVEPOINT ov")
        if not blocked:
            raise SystemExit(
                "OVERRIDE FAILED: built_by='cc-shipforge' did not block auditor 'cc-shipforge-1'. "
                "Rolling back."
            )
        print("  exercised: built_by override BLOCKS the named builder's lane (suffix included)")

        # 4. The floor must never be NULL — the failure mode 050's F1 fixed on the decider axis.
        cur.execute(
            "SELECT decision_audit_effective_builder(NULL, 'orch-console'), "
            "       decision_audit_effective_builder('', 'orch-console'), "
            "       decision_audit_effective_builder('cc-shipforge', 'orch-console')"
        )
        floor_null, floor_blank, override = cur.fetchone()
        if floor_null != "orch-console" or floor_blank != "orch-console" or override != "cc-shipforge":
            raise SystemExit(
                f"FLOOR FAILED: got {(floor_null, floor_blank, override)!r}. The builder axis could "
                "switch itself off. Rolling back."
            )
        print("  exercised: effective-builder floor is never NULL (NULL/blank -> assigner)")
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

            # The live assignments must SURVIVE the tightening. If cc-storefront (assigned before
            # this migration) would now be refused, the fix has invalidated the fleet's real audit
            # queue and I need to know inside the transaction, not from a confused auditor later.
            cur.execute(
                "SELECT da.decision_ref, da.auditor_agent, "
                "       decision_audit_effective_builder(sd.built_by, da.assigned_by) AS builder, "
                "       decision_audit_conflict(da.auditor_agent, sd.decided_by) AS vs_decider, "
                "       decision_audit_conflict(da.auditor_agent, "
                "           decision_audit_effective_builder(sd.built_by, da.assigned_by)) AS vs_builder "
                "  FROM decision_audits da "
                "  JOIN strategic_decisions sd ON sd.decision_ref = da.decision_ref "
                " WHERE da.completed_at IS NULL"
            )
            live = cur.fetchall()
            bad = [r for r in live if r[3] or r[4]]
            print("live open assignments under the new rule:")
            for ref, aud, builder, vd, vb in live:
                mark = "  <-- NOW CONFLICTED" if (vd or vb) else ""
                print(f"  {ref:16} {aud:16} builder={builder:16} decider={vd!s:5} builder_conflict={vb!s:5}{mark}")
            if bad:
                raise SystemExit(
                    f"post-condition FAILED: {len(bad)} LIVE open assignment(s) become conflicted "
                    "under this rule. Re-assign them BEFORE applying. Rolling back."
                )

            # Label/boolean coherence + no NULL bucket, re-asserted because the view was rewritten.
            cur.execute(
                "SELECT count(*) FROM decision_audit_state "
                "WHERE is_audit_closed <> (audit_state = 'AUDITED-ACCEPTED') OR audit_state IS NULL"
            )
            (drift,) = cur.fetchone()
            if drift:
                raise SystemExit(f"post-condition FAILED: {drift} drift/NULL rows. Rolling back.")

        print("applied 052_audit_builder_independence.sql")
        print("EXERCISED: the actual CAI-988 failure is now REFUSED; independent auditors still permitted")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
