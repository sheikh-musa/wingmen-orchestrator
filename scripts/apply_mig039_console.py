#!/usr/bin/env python3
"""apply_mig039_console.py — ORCH-CONSOLE guarded §6.6 apply of mig-039.

Executor picked by cai CAI-RESP-765 (decision 2071): build!=apply. Applies
migrations/039_governance_tables_revoke_default_dml.sql — REVOKE-FIRST hardening
that closes a real hole the SRE's CAI-763 effective-grant gate found: the bus DB's
ALTER DEFAULT PRIVILEGES auto-grants service_role + authenticated FULL DML on every
new table, so despite RLS + SELECT-only grants a bypassrls service_role key could
WRITE the kill-authority tables (remove the hub from protection then reap it) or
ERASE admin_offline_audit. This revokes INSERT/UPDATE/DELETE/TRUNCATE on
protected_agents + reaper_actors and UPDATE/DELETE/TRUNCATE on admin_offline_audit
(append-only) from service_role/authenticated/anon. fleet_reaper (SELECT gov +
INSERT audit) and postgres (owner DML) unchanged.

REVOKE-FIRST: must land BEFORE the reaper caller-cutover so the audit is
tamper-hardened before real reap rows arrive. Idempotent (REVOKE of an unheld priv
is a no-op); all statements run as owner (postgres), no role switching.

Same guarded contract: byte-bound sha256, grant-check, residency guard, managed txn
with pre/post EFFECTIVE-grant verify + atomic ledger. NEVER db push.

Modes: --prove-refusal | --check-only | (default apply).
Env: DATABASE_URL (substrate/bus silo tscuymavysscrvoberrr — decisions AND target)
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone

import psycopg

MIGRATION_FILE = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "migrations", "039_governance_tables_revoke_default_dml.sql"))
EXACT_FILENAME = "039_governance_tables_revoke_default_dml.sql"
EXPECT_SHA256 = "8bc6b5cbda0e0a6667973dfbcb490472cbeb69501d50ed8082087c0f11180515"
EXPECT_REF = "tscuymavysscrvoberrr"
AGENT_ID = "orch-console"
REFUSE_CHALLENGE = {"open", "challenged", "pending", "under_challenge"}
RLS_TIER = ("service_role", "authenticated", "anon")


class Refuse(Exception):
    pass


def _dsn() -> str:
    return os.environ.get("DATABASE_URL") or os.environ["SUPABASE_DB_URL"]


def file_sha256() -> str:
    with open(MIGRATION_FILE, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sha256_check() -> str:
    got = file_sha256()
    if got != EXPECT_SHA256:
        raise Refuse(f"sha256 DRIFT: file={got} != granted {EXPECT_SHA256} — grant is byte-bound; REFUSING.")
    print(f"✓ sha256 byte-bound check PASSED: {got}")
    return got


def grant_check(filename: str) -> dict:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, title, execution_status, status, challengeable_until,
                      challenge_status, superseded_by
               FROM strategic_decisions
               WHERE (title ILIKE %s OR decision ILIKE %s)
               ORDER BY id DESC""",
            (f"%{filename}%", f"%{filename}%"),
        )
        cols = ["id", "title", "execution_status", "status", "challengeable_until",
                "challenge_status", "superseded_by"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if not rows:
        raise Refuse(f"no strategic_decision names {filename} — grant not issued.")
    granted = [r for r in rows
               if (r["execution_status"] or "").lower() == "granted" and not r["superseded_by"]]
    if not granted:
        raise Refuse(f"decision(s) for {filename} exist but none execution_status='granted' "
                     f"(got {[(r['id'], r['execution_status']) for r in rows]}).")
    d = granted[0]
    cu = d["challengeable_until"]
    if not cu:
        raise Refuse(f"decision {d['id']} has no challengeable_until — window state unknown.")
    if cu > datetime.now(timezone.utc):
        raise Refuse(f"decision {d['id']} challenge window still OPEN until {cu}.")
    cs = (d["challenge_status"] or "").lower()
    if cs in REFUSE_CHALLENGE:
        raise Refuse(f"decision {d['id']} challenge_status={cs!r} is open.")
    print(f"✓ grant-check PASSED: decision {d['id']} granted, window closed ({cu}), "
          f"challenge_status={cs or 'none'}.")
    return d


def migration_body() -> str:
    with open(MIGRATION_FILE) as f:
        sql = f.read()
    kept = [ln for ln in sql.splitlines() if ln.strip().upper().rstrip() not in ("BEGIN;", "COMMIT;")]
    body = "\n".join(kept)
    TXN_CTL = {"BEGIN;", "COMMIT;", "ROLLBACK;", "START TRANSACTION;", "END TRANSACTION;"}
    for ln in body.splitlines():
        if ln.strip().upper() in TXN_CTL:
            raise Refuse(f"top-level transaction-control statement survived the strip: {ln!r}")
    return body


def _dml_grants(cur, table: str, grantees: tuple, privs: tuple) -> set:
    cur.execute(
        """SELECT grantee, privilege_type FROM information_schema.role_table_grants
           WHERE table_schema='public' AND table_name=%s
             AND grantee = ANY(%s) AND privilege_type = ANY(%s)""",
        (table, list(grantees), list(privs)),
    )
    return {(g, p) for (g, p) in cur.fetchall()}


def apply() -> dict:
    dsn = _dsn()
    if EXPECT_REF not in dsn:
        raise Refuse(f"DATABASE_URL does not target {EXPECT_REF} (residency guard).")
    sha = sha256_check()
    d = grant_check(EXACT_FILENAME)
    body = migration_body()
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (AGENT_ID,))

        # apply (idempotent REVOKEs, owner-run)
        cur.execute(body)

        # post-verify EFFECTIVE grants (the method that found the hole):
        #  protected_agents + reaper_actors: RLS tier holds ZERO of I/U/D/T
        for tbl in ("protected_agents", "reaper_actors"):
            g = _dml_grants(cur, tbl, RLS_TIER, ("INSERT", "UPDATE", "DELETE", "TRUNCATE"))
            if g:
                conn.rollback(); raise Refuse(f"post-verify: {tbl} still has RLS-tier DML {sorted(g)} — rolled back.")
        #  admin_offline_audit: RLS tier holds ZERO of U/D/T (append-only; INSERT may remain)
        g = _dml_grants(cur, "admin_offline_audit", RLS_TIER, ("UPDATE", "DELETE", "TRUNCATE"))
        if g:
            conn.rollback(); raise Refuse(f"post-verify: admin_offline_audit still erasable by RLS tier {sorted(g)} — rolled back.")
        #  invariants that must be PRESERVED: fleet_reaper INSERT on audit; postgres DML on all three
        g = _dml_grants(cur, "admin_offline_audit", ("fleet_reaper",), ("INSERT",))
        if ("fleet_reaper", "INSERT") not in g:
            conn.rollback(); raise Refuse("post-verify: fleet_reaper LOST INSERT on admin_offline_audit — the fn could not write reaps; rolled back.")
        for tbl in ("protected_agents", "reaper_actors", "admin_offline_audit"):
            g = _dml_grants(cur, tbl, ("postgres",), ("INSERT", "UPDATE", "DELETE"))
            if not {("postgres", "INSERT"), ("postgres", "UPDATE"), ("postgres", "DELETE")}.issubset(g):
                conn.rollback(); raise Refuse(f"post-verify: postgres (owner) lost DML on {tbl} — rolled back.")

        # ledger — same txn (atomic)
        cur.execute(
            """INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by)
               VALUES (%s, %s, %s, %s, %s)""",
            ("orchestrator", EXACT_FILENAME, EXPECT_REF, sha, AGENT_ID),
        )
        conn.commit()
    print(f"\n✓ APPLIED {EXACT_FILENAME} to {EXPECT_REF} under grant {d['id']} (sha256 {sha[:12]}…). "
          f"RLS tier (service_role/authenticated/anon) lost DML on protected_agents+reaper_actors and "
          f"erase-rights on admin_offline_audit; fleet_reaper INSERT + postgres owner-DML preserved; ledger recorded.")
    return d


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "--prove-refusal":
            bogus = "039_NONEXISTENT_prove_refusal.sql"
            try:
                grant_check(bogus)
            except Refuse as e:
                print(f"✓ PROVE-REFUSAL PASSED: gate correctly REFUSED an ungranted file — {e}")
                return 0
            print("✗ PROVE-REFUSAL FAILED: gate did NOT refuse a bogus filename — DO NOT TRUST.")
            return 3
        elif mode == "--check-only":
            sha256_check()
            grant_check(EXACT_FILENAME)
            print("(check-only: sha256 + grant valid; NOT applying.)")
            return 0
        else:
            apply()
            return 0
    except Refuse as e:
        print(f"\n✗ REFUSE (did not apply): {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
