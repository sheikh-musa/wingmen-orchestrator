#!/usr/bin/env python3
"""Apply migrations/030_rls_lockdown_3_tables.sql to the substrate (direct psycopg —
decision-962: the supabase CLI shadow-diff path is forbidden against prod).

CAI-RESP-509: completes the RLS shape on fleet_stall_state / portfolio_entries /
site_content. The migration file owns no BEGIN/COMMIT; this applier owns the
transaction so --dry-run can execute-then-ROLLBACK (nothing persists).

Usage:
  scripts/apply_rls_lockdown_030.py [--dry-run]

Both modes run the same verification (RLS on, public-read intact on the 2, full-lock
on fleet_stall_state, no public write grants). --dry-run rolls back before verifying
against the *uncommitted* transaction; real mode commits, then re-verifies committed.
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

MIG = os.path.join(ROOT, "migrations", "030_rls_lockdown_3_tables.sql")
TABLES = ("fleet_stall_state", "portfolio_entries", "site_content")
READABLE = ("portfolio_entries", "site_content")


def verify(cur) -> list[str]:
    """Return a list of human-readable verification lines; raises on hard failure."""
    out: list[str] = []

    # relrowsecurity per table
    cur.execute(
        "SELECT c.relname, c.relrowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname = ANY(%s) ORDER BY c.relname",
        (list(TABLES),),
    )
    rls = dict(cur.fetchall())
    for t in TABLES:
        out.append(f"  RLS relrowsecurity[{t}] = {rls.get(t)}")
        assert rls.get(t) is True, f"RLS not enabled on {t}"

    # policies
    cur.execute(
        "SELECT tablename, policyname, roles, cmd FROM pg_policies "
        "WHERE schemaname='public' AND tablename = ANY(%s) ORDER BY tablename, policyname",
        (list(TABLES),),
    )
    pols = cur.fetchall()
    for p in pols:
        out.append(f"  policy {p[0]}.{p[1]} roles={p[2]} cmd={p[3]}")

    # public-read policies present on the 2 readable tables
    names = {(p[0], p[1]) for p in pols}
    for t in READABLE:
        assert (t, f"{t}_public_read") in names, f"missing {t}_public_read policy"
    # fleet_stall_state has NO non-service policy
    fss = [p[1] for p in pols if p[0] == "fleet_stall_state"]
    assert fss == ["fleet_stall_state_service_only"], f"fleet_stall_state policies unexpected: {fss}"

    # table grants to public roles — none for fleet_stall_state; SELECT-only for the 2
    cur.execute(
        "SELECT table_name, grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND table_name = ANY(%s) "
        "AND grantee IN ('anon','authenticated','PUBLIC') ORDER BY table_name, grantee, privilege_type",
        (list(TABLES),),
    )
    grants = cur.fetchall()
    for g in grants:
        out.append(f"  grant {g[0]} -> {g[1]}: {g[2]}")
    fss_grants = [g for g in grants if g[0] == "fleet_stall_state"]
    assert not fss_grants, f"fleet_stall_state still grants to public roles: {fss_grants}"
    # no write grant to public roles on ANY of the three
    writes = [g for g in grants if g[2] in ("DELETE", "INSERT", "UPDATE", "TRUNCATE")]
    assert not writes, f"unexpected public write grants: {writes}"
    # the 2 readable keep SELECT for anon
    for t in READABLE:
        assert (t, "anon", "SELECT") in grants, f"{t} lost anon SELECT grant"

    # live SET ROLE reads: anon can read the 2, cannot read fleet_stall_state
    for t in READABLE:
        cur.execute("SET LOCAL ROLE anon")
        cur.execute(f'SELECT count(*) FROM public."{t}"')
        n = cur.fetchone()[0]
        cur.execute("RESET ROLE")
        out.append(f"  [SET ROLE anon] SELECT count(*) FROM {t} = {n}  (read OK)")
    # anon read of fleet_stall_state must be denied (permission error)
    cur.execute("SET LOCAL ROLE anon")
    try:
        cur.execute('SELECT count(*) FROM public."fleet_stall_state"')
        cur.fetchone()
        cur.execute("RESET ROLE")
        raise AssertionError("anon was able to read fleet_stall_state — NOT locked!")
    except psycopg.errors.InsufficientPrivilege:
        # expected — connection is now in aborted state; roll back to savepoint below
        out.append("  [SET ROLE anon] SELECT FROM fleet_stall_state -> DENIED (InsufficientPrivilege) — locked")
        raise _AnonDenied(out)
    return out


class _AnonDenied(Exception):
    """Carries the accumulated verify lines up past the aborted-txn boundary."""
    def __init__(self, lines):
        self.lines = lines


def run_verify(conn) -> list[str]:
    """Verify inside a savepoint so the expected anon-denied error doesn't poison
    the outer transaction. Returns the verification lines."""
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT vfy")
        try:
            return verify(cur)
        except _AnonDenied as d:
            cur.execute("ROLLBACK TO SAVEPOINT vfy")
            return d.lines


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL / SUPABASE_DB_URL not set in .env")
        return 1
    sql = open(MIG).read()

    mode = "DRY-RUN (rollback)" if dry else "APPLY (commit)"
    print(f"== 030_rls_lockdown_3_tables — {mode} ==")
    with psycopg.connect(dsn) as conn:  # autocommit=False → explicit txn control
        with conn.cursor() as cur:
            cur.execute(sql)
        print("  migration SQL executed (assertion gate passed).")
        # verify against the uncommitted transaction (valid in both modes)
        lines = run_verify(conn)
        if dry:
            conn.rollback()
            print("  ROLLED BACK (dry-run) — nothing persisted.")
        else:
            conn.commit()
            print("  COMMITTED.")
            # re-verify against the now-committed state (fresh txn)
            lines = run_verify(conn)
    print("\n-- verification --")
    for ln in lines:
        print(ln)
    print(f"\n030 {'dry-run OK' if dry else 'applied + verified'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
