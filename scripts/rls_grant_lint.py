#!/usr/bin/env python3
"""rls_grant_lint.py — mechanical grant/policy lint that closes PII-ANON-1 (CAI-RESP-511).

Reviews keep MISSING the anon-exposure class because they read POLICIES, not GRANTS —
and TRUNCATE is RLS-immune (gated only by the table GRANT, never by a policy). This lint
checks the substrate the way an anon caller actually hits it, per public table:

  CRITICAL (exit 1) — the exploitable class:
    1. RLS disabled on a base table (not in RLS_OFF_OK).
    2. anon / authenticated / PUBLIC holds TRUNCATE (RLS-immune; always forbidden).
    3. A PERMISSIVE policy with qual=true OR with_check=true for anon/public on a
       WRITE or ALL command (the mis-named-'service role' USING(true) hole).
    4. anon or PUBLIC holds INSERT/UPDATE/DELETE on a table NOT in ANON_WRITE_OK.
    5. The postgres-owned DEFAULT privileges grant anon write/TRUNCATE to new tables.

  WARN (report, exit 0 unless --strict) — expected-but-worth-eyes:
    - `authenticated` INSERT/UPDATE/DELETE grants: the app's RLS-scoped write path.
      Legitimate, but listed so a NEW one is visible, not lost in the 77.
    - Allowlisted anon-write tables (deliberate telemetry).
    - The supabase_admin-owned default ACL anon-write residual (needs a dashboard /
      supabase_admin session to close — unreachable from a postgres session; see
      migrations/031_anon_write_truncate_lockdown.sql).

Read allowlists below before widening them — every entry is an intentional exception
with a reason. SELECT is never failed here (public-read via USING(true) is legitimate,
e.g. portfolio_entries / the cryptographic-transparency tables); this lint is about the
WRITE + TRUNCATE surface.

Usage:
  scripts/rls_grant_lint.py            # human report; exit 1 on any CRITICAL
  scripts/rls_grant_lint.py --strict   # also exit 1 if any WARN
  scripts/rls_grant_lint.py --json     # machine-readable findings
"""
import json
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

# ── allowlists (intentional exceptions — each needs a reason) ────────────────
# Tables intentionally left with RLS OFF (none today; migration 030 closed the last 3).
RLS_OFF_OK: set[str] = set()

# Tables where anon WRITE is a deliberate design choice (client-side telemetry ingest).
# ui_events: anonymous UI analytics — anon INSERT is the intended ingestion path.
ANON_WRITE_OK: set[str] = {"ui_events"}

WRITE_PRIVS = ("INSERT", "UPDATE", "DELETE")


def collect_findings(cur) -> list[dict]:
    findings: list[dict] = []

    def add(sev, code, table, detail):
        findings.append({"severity": sev, "code": code, "table": table, "detail": detail})

    # 1. RLS disabled on base tables
    cur.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity IS NOT TRUE "
        "ORDER BY c.relname"
    )
    for (t,) in cur.fetchall():
        if t not in RLS_OFF_OK:
            add("CRITICAL", "RLS_OFF", t, "row-level security is DISABLED on this base table")

    # 2. TRUNCATE grants to public callers (RLS-immune)
    cur.execute(
        "SELECT table_name, grantee FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND grantee IN ('anon','authenticated','PUBLIC') "
        "AND privilege_type='TRUNCATE' ORDER BY table_name, grantee"
    )
    for t, g in cur.fetchall():
        add("CRITICAL", "TRUNCATE_GRANT", t, f"{g} holds TRUNCATE (RLS-immune — anyone with that role can wipe the table)")

    # 3. permissive qual=true / with_check=true write policies for anon/public
    cur.execute(
        "SELECT tablename, policyname, roles, cmd, qual, with_check FROM pg_policies "
        "WHERE schemaname='public' AND permissive='PERMISSIVE' "
        "AND cmd IN ('ALL','INSERT','UPDATE','DELETE') ORDER BY tablename, policyname"
    )
    for t, pol, roles, cmd, qual, wc in cur.fetchall():
        role_set = set(roles or [])
        touches_public = bool(role_set & {"anon", "authenticated", "public", "PUBLIC"})
        if not touches_public:
            continue
        open_qual = qual == "true"
        open_check = wc == "true"
        if open_qual or open_check:
            if t in ANON_WRITE_OK:
                add("WARN", "ANON_WRITE_POLICY_ALLOWLISTED", t,
                    f"policy {pol!r} ({cmd}) is open (qual/with_check=true) for {sorted(role_set)} — allowlisted telemetry")
            else:
                which = ", ".join(x for x, on in (("USING(true)", open_qual), ("WITH CHECK(true)", open_check)) if on)
                add("CRITICAL", "OPEN_WRITE_POLICY", t,
                    f"PERMISSIVE policy {pol!r} on {cmd} is {which} for {sorted(role_set)} — anon/public can write every row")

    # 4. anon/PUBLIC write grants on non-allowlisted tables
    cur.execute(
        "SELECT table_name, grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND grantee IN ('anon','PUBLIC') "
        "AND privilege_type IN ('INSERT','UPDATE','DELETE') ORDER BY table_name, grantee, privilege_type"
    )
    for t, g, priv in cur.fetchall():
        if t in ANON_WRITE_OK:
            add("WARN", "ANON_WRITE_GRANT_ALLOWLISTED", t, f"{g} holds {priv} — allowlisted telemetry")
        else:
            add("CRITICAL", "ANON_WRITE_GRANT", t, f"{g} holds {priv} on a non-allowlisted table")

    # 4b. authenticated write grants — the app's RLS-scoped write path (WARN for visibility)
    cur.execute(
        "SELECT count(DISTINCT table_name) FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND grantee='authenticated' "
        "AND privilege_type IN ('INSERT','UPDATE','DELETE')"
    )
    n_auth = cur.fetchone()[0]
    if n_auth:
        add("WARN", "AUTHENTICATED_WRITE_GRANTS", "*",
            f"{n_auth} tables grant `authenticated` write (app RLS-scoped path — expected; "
            "listed so a new one is visible). Each is safe ONLY as long as its RLS policy is correct.")

    # 5. default privileges — postgres-owned (CRITICAL if regressed) + supabase_admin residual (WARN)
    for owner, sev, code in (("postgres", "CRITICAL", "DEFAULT_ANON_WRITE"),
                             ("supabase_admin", "WARN", "DEFAULT_ANON_WRITE_RESIDUAL")):
        cur.execute(
            "SELECT count(*) FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
            "WHERE d.defaclnamespace='public'::regnamespace AND d.defaclobjtype='r' "
            "AND d.defaclrole=%s::regrole AND a.grantee='anon'::regrole "
            "AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')",
            (owner,),
        )
        n = cur.fetchone()[0]
        if n:
            extra = "" if owner == "postgres" else " (needs a dashboard/supabase_admin session — unreachable from postgres)"
            add(sev, code, "*", f"{owner}-owned default privileges grant anon write/TRUNCATE to NEW public tables{extra}")

    return findings


def main() -> int:
    strict = "--strict" in sys.argv[1:]
    as_json = "--json" in sys.argv[1:]
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL / SUPABASE_DB_URL not set in .env", file=sys.stderr)
        return 2
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        findings = collect_findings(cur)

    crit = [f for f in findings if f["severity"] == "CRITICAL"]
    warn = [f for f in findings if f["severity"] == "WARN"]

    if as_json:
        print(json.dumps({"critical": crit, "warn": warn}, indent=2))
    else:
        print(f"== RLS grant lint (PII-ANON-1 / CAI-RESP-511) — {len(crit)} CRITICAL, {len(warn)} WARN ==")
        for f in crit:
            print(f"  CRITICAL [{f['code']}] {f['table']}: {f['detail']}")
        for f in warn:
            print(f"  warn     [{f['code']}] {f['table']}: {f['detail']}")
        if not crit:
            print("  no CRITICAL findings — anon/public write+TRUNCATE surface is closed.")

    if crit:
        return 1
    if warn and strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
