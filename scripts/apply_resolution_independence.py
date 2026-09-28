#!/usr/bin/env python3
"""Apply migrations/057_resolution_independence_and_third_path.sql via DIRECT psycopg.

cc-quality N1 (HIGH) + N2, both found INSIDE the 053 fix, one hour after it shipped.

NEVER `supabase db push` (decision 962). Target: the SUBSTRATE coordination-plane DB.

THE EXERCISE REPLAYS cc-quality's OWN PROOF. It silenced its own alarm by resolving its own
rejected row; the post-condition asserts that exact UPDATE now RAISES, and that a genuinely
independent resolver still succeeds -- a guard that refuses everyone is 047's bug with the
opposite sign.

Usage:  scripts/apply_resolution_independence.py [--dry-run]
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "057_resolution_independence_and_third_path.sql"
PROBE = "PROBE-057-RESOLUTION"


def _exercise(cur):
    cur.execute("SAVEPOINT probe")
    try:
        cur.execute(
            "INSERT INTO strategic_decisions (decision_ref,title,decision,reasoning,domain,"
            " decided_by,challenge_status,challengeable_until,bypass_review,source,audit_tier) "
            "VALUES (%s,'probe','probe','probe','operations','cai','challenge_window',"
            " now()+interval '1 day',true,'musa_direct','FULL')", (PROBE,))
        cur.execute(
            "INSERT INTO decision_audits (decision_ref,auditor_agent,assigned_by,lens,verdict,"
            " checks_performed,completed_at,assigned_at) "
            "VALUES (%s,'cc-quality','cai','design','rejected',%s,"
            " now()-interval '48 hours', now()-interval '72 hours') RETURNING id",
            (PROBE, "probe: replays cc-quality's N1 — the auditor resolving its own rejected row"))
        (aid,) = cur.fetchone()

        # N1: the auditor must NOT be able to resolve its own row. cc-quality PROVED it could.
        for who in ("cc-quality", "cc-quality-1"):
            cur.execute("SAVEPOINT n1")
            blocked = False
            try:
                cur.execute("UPDATE decision_audits SET resolved_at=now(), resolved_by=%s "
                            "WHERE id=%s", (who, aid))
            except Exception:
                blocked = True
            cur.execute("ROLLBACK TO SAVEPOINT n1")
            if not blocked:
                raise SystemExit(
                    f"N1 FAILED: auditor resolved its own audit as {who!r}. The alarm can still be "
                    "silenced by the body it watches. Rolling back.")
        print("  exercised: N1 — auditor CANNOT resolve its own row (cc-quality / cc-quality-1)")

        # ...and an independent resolver still must be able to.
        cur.execute("UPDATE decision_audits SET resolved_at=now(), resolved_by='cai', "
                    "resolution_note='probe' WHERE id=%s RETURNING resolved_by", (aid,))
        if not cur.fetchone():
            raise SystemExit("N1 FAILED: an independent resolver was refused. Rolling back.")
        print("  exercised: independent resolver (cai) still PERMITTED")

        # N2: audited clean, never closed -> must now escalate.
        cur.execute("UPDATE decision_audits SET verdict='accepted', resolved_at=NULL, "
                    "resolved_by=NULL, escalated_at=NULL WHERE id=%s", (aid,))
        cur.execute("SELECT decision_ref, action FROM escalate_stale_decision_audits()")
        fired = [r for r in cur.fetchall() if r[0] == PROBE]
        if not fired or fired[0][1] != "escalated_never_closed":
            raise SystemExit(
                f"N2 FAILED: audited-clean-but-never-closed did not escalate, got {fired!r}. "
                "Rolling back.")
        print("  exercised: N2 — audited clean + never closed NOW escalates")

        # And it must NOT close anything by itself.
        cur.execute("SELECT challenge_status FROM strategic_decisions WHERE decision_ref=%s", (PROBE,))
        if cur.fetchone()[0] != "challenge_window":
            raise SystemExit("N2 FAILED: the escalator CLOSED a decision. Rolling back.")
        print("  exercised: nothing was auto-closed (an auto-close is accepted_by_timeout recoated)")
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT probe")


def main() -> int:
    load_dotenv(ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL not set", file=sys.stderr); return 2
    sql = SQL_PATH.read_text()
    if "--dry-run" in sys.argv:
        print(f"dry-run: would apply {SQL_PATH.name} ({len(sql)} bytes)"); return 0
    import psycopg2
    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql)
            _exercise(cur)
            # No live row may already be self-resolved — if one is, the guard arrived too late
            # and I need to know inside the transaction.
            cur.execute("SELECT count(*) FROM decision_audits WHERE resolved_by IS NOT NULL "
                        "AND decision_audit_conflict(resolved_by, auditor_agent)")
            (selfres,) = cur.fetchone()
            if selfres:
                raise SystemExit(
                    f"post-condition FAILED: {selfres} live row(s) are ALREADY self-resolved. "
                    "Rolling back — those need review, not a silent guard on top.")
            print("  verified: 0 live rows were self-resolved before the guard existed")
        print("applied 057_resolution_independence_and_third_path.sql")
        print("EXERCISED: N1 self-resolution refused; N2 third accumulation path escalates")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
