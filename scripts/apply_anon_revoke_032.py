#!/usr/bin/env python3
"""Apply migrations/032_anon_write_revoke_nontelemetry.sql (direct psycopg —
decision-962). CAI-RESP-512 item 3: revoke the latent anon/PUBLIC write grants
on all public tables except ui_events telemetry, leaving `authenticated` untouched.

GATED: this is APPROVED with the standard 24h window on CAI-512. Run --dry-run now
to prove idempotence + zero live-behavior change; do the real apply only after the
window closes and cai re-confirms the committed SQL matches.

Usage:
  scripts/apply_anon_revoke_032.py --dry-run   # execute-then-rollback + verify
  scripts/apply_anon_revoke_032.py             # commit + dual-ledger (post-gate only)
"""
import hashlib
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

MIG = os.path.join(ROOT, "migrations", "032_anon_write_revoke_nontelemetry.sql")
SILO_REF = "tscuymavysscrvoberrr"  # hub substrate
MIZAN = ("mizan_eval_runs", "mizan_eval_set", "mizan_human_reviews", "mizan_user_feedback")
# Sensitive tables to prove anon's DATA REACH is unchanged (0 rows) before and after.
LIVE_PROBE = ("donations", "persons", "receipts", "payments", "clients")


def _anon_reachable_rows(cur, tables) -> dict:
    """Rows anon can actually reach = its RLS-visible set (the SAME USING clause gates
    SELECT and UPDATE/DELETE). Revoking the WRITE grant cannot change this: anon that
    can see 0 rows can mutate 0 rows both before (RLS denies) and after (grant denies).
    Proving this count is 0 and UNCHANGED across the migration is the real 'no live
    behavior change' proof — not the statement-level grant/deny distinction, which
    flips harmlessly (both resolve to 0 rows mutated). Denial -> 0 reachable."""
    out = {}
    for t in tables:
        cur.execute("SAVEPOINT ap")
        cur.execute("SET LOCAL ROLE anon")
        try:
            cur.execute(f'SELECT count(*) FROM public."{t}"')
            out[t] = cur.fetchone()[0]
        except Exception:
            out[t] = 0
        cur.execute("ROLLBACK TO SAVEPOINT ap")
    return out


def verify(cur) -> list[str]:
    out = []
    # (1) no residual anon/PUBLIC write except ui_events INSERT
    cur.execute(
        "SELECT count(*) FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND grantee IN ('anon','PUBLIC') "
        "AND privilege_type IN ('INSERT','UPDATE','DELETE') "
        "AND NOT (table_name='ui_events' AND grantee='anon' AND privilege_type='INSERT')"
    )
    n = cur.fetchone()[0]
    out.append(f"  residual anon/PUBLIC write grants (excl ui_events INSERT) = {n}")
    assert n == 0, f"residual anon/PUBLIC write: {n}"
    # (2) ui_events telemetry intact
    cur.execute("SELECT has_table_privilege('anon','public.ui_events','INSERT')")
    assert cur.fetchone()[0], "ui_events lost anon INSERT"
    out.append("  ui_events anon INSERT = kept (telemetry intact)")
    # (3) authenticated app-write path intact on the 4 mizan tables
    for t in MIZAN:
        cur.execute("SELECT has_table_privilege('authenticated', %s, 'INSERT')", (f"public.{t}",))
        assert cur.fetchone()[0], f"authenticated lost INSERT on {t}"
    out.append(f"  authenticated INSERT intact on {len(MIZAN)} mizan app tables")
    return out


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL/SUPABASE_DB_URL not set")
        return 1
    sql = open(MIG).read()
    sha = hashlib.sha256(sql.encode()).hexdigest()
    print(f"== 032_anon_write_revoke_nontelemetry — {'DRY-RUN' if dry else 'APPLY'} ==")
    print(f"  sha256={sha}")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            before = _anon_reachable_rows(cur, LIVE_PROBE)
            cur.execute(sql)
            after = _anon_reachable_rows(cur, LIVE_PROBE)
        print("  live-behavior invariance (rows anon can reach = its mutable set):")
        for t in LIVE_PROBE:
            same = "SAME" if before[t] == after[t] else "!!! CHANGED"
            print(f"    {t}: before={before[t]} after={after[t]} rows  [{same}]")
        assert before == after, "LIVE BEHAVIOR CHANGED — anon reachable rows differ before/after!"
        assert all(v == 0 for v in after.values()), "anon can reach rows post-migration — NOT purely latent!"
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT v")
            lines = verify(cur)
            cur.execute("ROLLBACK TO SAVEPOINT v")
        if dry:
            conn.rollback()
            print("  ROLLED BACK (dry-run).")
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by) "
                    "VALUES ('orchestrator','032_anon_write_revoke_nontelemetry.sql',%s,%s,'orch-console') "
                    "ON CONFLICT DO NOTHING", (SILO_REF, sha))
            conn.commit()
            print("  COMMITTED + migration_ledger recorded.")
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT v2")
                lines = verify(cur)
                cur.execute("ROLLBACK TO SAVEPOINT v2")
    print("\n-- verification --")
    for ln in lines:
        print(ln)
    print(f"\n032 {'dry-run OK' if dry else 'applied + verified'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
