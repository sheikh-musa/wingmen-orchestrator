#!/usr/bin/env python3
"""Apply migrations/031_anon_write_truncate_lockdown.sql (direct psycopg —
decision-962: the supabase CLI shadow-diff path is forbidden against prod).

CAI-RESP-511: formalizes cai's two live emergency fixes (mass TRUNCATE REVOKE +
5 policy re-gate) into the ledger and adds the default-privileges hardening. The
migration file owns no BEGIN/COMMIT; this applier owns the transaction so
--dry-run can execute-then-ROLLBACK (nothing persists).

Usage:
  scripts/apply_anon_lockdown_031.py [--dry-run]

Verification (both modes): no anon/auth/PUBLIC TRUNCATE grant on any public table;
the 5 policies gate on service_role; new-table default privileges drop anon write;
and a LIVE `SET ROLE anon; TRUNCATE ...` is denied (InsufficientPrivilege).
On real apply it also records the migration_ledger row (repo/name/sha256/applied_by).
"""
import hashlib
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

MIG = os.path.join(ROOT, "migrations", "031_anon_write_truncate_lockdown.sql")
FIVE = ("clients", "payments", "chat_history", "pending_signups", "site_templates")
SILO_REF = "ceayjeamtmcyzzvqflus"  # ihsanos multi-tenant DB (the substrate this migration hardens)


class _AnonTruncDenied(Exception):
    def __init__(self, lines):
        self.lines = lines


def verify(cur) -> list[str]:
    out: list[str] = []

    # (1) no anon/auth/PUBLIC TRUNCATE grant anywhere in public
    cur.execute(
        "SELECT count(*) FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND grantee IN ('anon','authenticated','PUBLIC') "
        "AND privilege_type='TRUNCATE'"
    )
    n_trunc = cur.fetchone()[0]
    out.append(f"  anon/auth/PUBLIC TRUNCATE grants on public tables = {n_trunc}")
    assert n_trunc == 0, f"residual TRUNCATE grants: {n_trunc}"

    # (2) the 5 policies gate on service_role in qual AND with_check
    cur.execute(
        "SELECT tablename, qual, with_check FROM pg_policies "
        "WHERE schemaname='public' AND policyname='service role full access' "
        "AND tablename = ANY(%s) ORDER BY tablename",
        (list(FIVE),),
    )
    rows = cur.fetchall()
    seen = {r[0] for r in rows}
    for t in FIVE:
        assert t in seen, f"missing 'service role full access' policy on {t}"
    want = "(auth.role() = 'service_role'::text)"
    for t, qual, wc in rows:
        out.append(f"  policy {t}: qual={qual} check={wc}")
        assert qual == want and wc == want, f"{t} policy not service-role-gated"

    # (3) default privileges drop anon write/TRUNCATE for new tables
    cur.execute(
        "SELECT count(*) FROM pg_default_acl d "
        "CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
        "WHERE d.defaclnamespace='public'::regnamespace AND d.defaclobjtype='r' "
        "AND d.defaclrole='postgres'::regrole "
        "AND a.grantee='anon'::regrole AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')"
    )
    n_def = cur.fetchone()[0]
    out.append(f"  postgres-owned default-priv anon write/TRUNCATE entries (new tables) = {n_def}")
    assert n_def == 0, "postgres-owned default privileges still grant anon write/TRUNCATE"
    # supabase_admin-owned default ACL is a platform residual (see migration note) —
    # report it, do not fail on it (unreachable from a postgres session).
    cur.execute(
        "SELECT count(*) FROM pg_default_acl d "
        "CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
        "WHERE d.defaclnamespace='public'::regnamespace AND d.defaclobjtype='r' "
        "AND d.defaclrole='supabase_admin'::regrole "
        "AND a.grantee='anon'::regrole AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')"
    )
    out.append(f"  [residual] supabase_admin-owned default anon-write entries (needs dashboard) = {cur.fetchone()[0]}")

    # (4) LIVE proof: anon cannot TRUNCATE a real table (use a low-value one)
    cur.execute("SAVEPOINT anon_trunc")
    cur.execute("SET LOCAL ROLE anon")
    try:
        cur.execute("TRUNCATE TABLE public.ui_events")
        cur.execute("RESET ROLE")
        raise AssertionError("anon was able to TRUNCATE ui_events — NOT locked!")
    except psycopg.errors.InsufficientPrivilege:
        out.append("  [SET ROLE anon] TRUNCATE ui_events -> DENIED (InsufficientPrivilege) — locked")
        raise _AnonTruncDenied(out)


def run_verify(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT vfy")
        try:
            verify(cur)
            return []  # unreachable — verify always raises _AnonTruncDenied on success path
        except _AnonTruncDenied as d:
            cur.execute("ROLLBACK TO SAVEPOINT vfy")
            return d.lines


def record_ledger(conn, sha: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by) "
            "VALUES ('orchestrator', '031_anon_write_truncate_lockdown.sql', %s, %s, 'orch-console') "
            "ON CONFLICT DO NOTHING",
            (SILO_REF, sha),
        )


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL / SUPABASE_DB_URL not set in .env")
        return 1
    sql = open(MIG).read()
    sha = hashlib.sha256(sql.encode()).hexdigest()

    mode = "DRY-RUN (rollback)" if dry else "APPLY (commit)"
    print(f"== 031_anon_write_truncate_lockdown — {mode} ==")
    print(f"  sha256={sha}")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        print("  migration SQL executed (assertion gate passed).")
        lines = run_verify(conn)
        if dry:
            conn.rollback()
            print("  ROLLED BACK (dry-run) — nothing persisted.")
        else:
            record_ledger(conn, sha)
            conn.commit()
            print("  COMMITTED + migration_ledger row recorded.")
            lines = run_verify(conn)
    print("\n-- verification --")
    for ln in lines:
        print(ln)
    print(f"\n031 {'dry-run OK' if dry else 'applied + verified'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
