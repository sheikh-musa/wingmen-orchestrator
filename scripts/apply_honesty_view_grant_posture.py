#!/usr/bin/env python3
"""Apply migrations/051_honesty_view_grant_posture.sql via DIRECT psycopg.

cc-quality #23761 (HIGH): the three honesty views bypassed their base tables' RLS.

NEVER `supabase db push` (decision 962). Target: the SUBSTRATE coordination-plane DB.

THE POST-CONDITION IS THE WHOLE POINT AND IT IS EXECUTED, NOT REASONED. cc-quality proved the
hole by actually reading 1353 governance rows AS anon, not by inspecting reloptions. So this
script proves the fix the same way: it SETs ROLE to anon and to authenticated and asserts the
read is now REFUSED. An access-control fix verified by reading its own configuration is the
"never green on absence-of-signal" defect wearing a security hat.

Usage:  scripts/apply_honesty_view_grant_posture.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "051_honesty_view_grant_posture.sql"

VIEWS = ("decision_audit_state", "lane_tasks_state", "invariant_registry_state")


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
            # BEFORE, so the fix is measured as a CHANGE rather than asserted as a state.
            before = {}
            for v in VIEWS:
                cur.execute("SAVEPOINT b")
                try:
                    cur.execute("SET LOCAL ROLE anon")
                    cur.execute(f"SELECT count(*) FROM {v}")
                    before[v] = cur.fetchone()[0]
                except Exception:
                    before[v] = None
                cur.execute("ROLLBACK TO SAVEPOINT b")
            print("BEFORE — rows visible to anon:")
            for v in VIEWS:
                print(f"  {v:28} {before[v] if before[v] is not None else 'denied'}")

            cur.execute(sql)

            # (1) THE CONTROL, EXECUTED: anon and authenticated must now be REFUSED.
            for role in ("anon", "authenticated"):
                for v in VIEWS:
                    cur.execute("SAVEPOINT p")
                    leaked = None
                    try:
                        cur.execute(f"SET LOCAL ROLE {role}")
                        cur.execute(f"SELECT count(*) FROM {v}")
                        leaked = cur.fetchone()[0]
                    except Exception:
                        pass
                    cur.execute("ROLLBACK TO SAVEPOINT p")
                    if leaked is not None:
                        raise SystemExit(
                            f"post-condition FAILED: role {role} can still read {v} "
                            f"({leaked} rows). Rolling back."
                        )
            print("VERIFIED: anon and authenticated are REFUSED on all three views (executed, not inspected)")

            # (2) security_invoker actually set — the structural half.
            cur.execute(
                "SELECT relname, reloptions FROM pg_class WHERE relname = ANY(%s)", (list(VIEWS),)
            )
            for name, opts in cur.fetchall():
                if not opts or not any("security_invoker=on" in o for o in opts):
                    raise SystemExit(
                        f"post-condition FAILED: {name} reloptions={opts!r}, security_invoker not on. "
                        "Rolling back."
                    )
            print("VERIFIED: security_invoker=on on all three views")

            # (3) THE OTHER DIRECTION — the fix must not silently blind the console. A grant fix
            #     that turns over-exposure into under-exposure is still a broken board.
            for t in ("lane_tasks", "strategic_decisions", "decision_audits", "invariant_registry"):
                cur.execute("SELECT has_table_privilege('console_readonly', %s, 'SELECT')", (t,))
                if not cur.fetchone()[0]:
                    raise SystemExit(
                        f"post-condition FAILED: console_readonly lost SELECT on {t}; with "
                        "security_invoker=on the board would read empty. Rolling back."
                    )
            for v in VIEWS:
                cur.execute("SELECT has_table_privilege('console_readonly', %s, 'SELECT')", (v,))
                if not cur.fetchone()[0]:
                    raise SystemExit(
                        f"post-condition FAILED: console_readonly cannot SELECT {v}. Rolling back."
                    )
            print("VERIFIED: console_readonly retains SELECT on all three views AND their base tables")

            # Owner-side row counts, kept for the POST-COMMIT console comparison below.
            expected = {}
            for v in VIEWS:
                cur.execute(f"SELECT count(*) FROM {v}")
                expected[v] = cur.fetchone()[0]

            # (4) The write-capable functions must not be PUBLIC-executable. The SECURITY DEFINER
            #     one inserts bus rows, so a PUBLIC EXECUTE there is a bus-write primitive.
            for fn, args in (
                ("close_decision_by_audit", "text, text"),
                ("escalate_stale_decision_audits", ""),
            ):
                for role in ("public", "anon", "authenticated"):
                    cur.execute(
                        f"SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                        (role, f"public.{fn}({args})"),
                    )
                    if cur.fetchone()[0]:
                        raise SystemExit(
                            f"post-condition FAILED: {role} can still EXECUTE {fn}. Rolling back."
                        )
            print("VERIFIED: write-capable functions not executable by PUBLIC/anon/authenticated")

        print("applied 051_honesty_view_grant_posture.sql")
        print(f"  closed on: {', '.join(VIEWS)}")

        # ---- POST-COMMIT: read the views as the CONSOLE actually does -----------------------
        # This MUST run after commit. The first version ran it inside the transaction and hung:
        # the console is a separate connection and was blocked on the ALTER VIEW locks this very
        # txn was holding, so it timed out rather than answering. A verification that deadlocks
        # against its own subject is not a verification.
        #
        # WHY IT EXISTS AT ALL: the in-txn check asserts has_table_privilege -- the GRANT. A grant
        # is permission to ASK; an RLS policy is permission to SEE. Checking the grant and
        # reporting "verified" is how invariant_registry_state ended up returning 0 rows to the
        # console instead of 34. A board that reads empty is as wrong as one that reads
        # everything, and this is the read that can tell the difference.
        console_dsn = os.environ.get("CONSOLE_DB_URL")
        if not console_dsn:
            print("WARNING: CONSOLE_DB_URL not set — console-side read NOT verified.", file=sys.stderr)
            return 1
        cconn = psycopg2.connect(console_dsn)
        try:
            with cconn.cursor() as ccur:
                ccur.execute("SELECT current_user")
                who = ccur.fetchone()[0]
                bad = []
                for v in VIEWS:
                    ccur.execute(f"SELECT count(*) FROM {v}")
                    got = ccur.fetchone()[0]
                    if got != expected[v]:
                        bad.append(f"{v}: console sees {got}, owner sees {expected[v]}")
                    else:
                        print(f"  as {who}: {v:28} {got} rows  OK")
                if bad:
                    print("\nCONSOLE-SIDE VERIFICATION FAILED — the fix blinded the board:",
                          file=sys.stderr)
                    for b in bad:
                        print(f"  {b}", file=sys.stderr)
                    print("The grant posture is committed; fix the RLS policy forward.",
                          file=sys.stderr)
                    return 1
        finally:
            cconn.close()
        print("VERIFIED post-commit: the console reads every board in full")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
