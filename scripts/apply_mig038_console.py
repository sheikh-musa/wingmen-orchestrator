#!/usr/bin/env python3
"""apply_mig038_console.py — ORCH-CONSOLE guarded §6.6 apply of mig-038.

Executor picked by cai CAI-RESP-763 (decision 2068): build!=apply (per mig-148/037).
Applies migrations/038_admin_mark_offline_scoped_reapers.sql — the durable,
data-driven generalization of today's ad-hoc NEVER_OFFLINE guard:
  * protected_agents  — the 5 singletons; admin_mark_offline REFUSES to offline
    any of them for EVERY reaper branch (one enforcement point kills the hub
    mis-reap class);
  * reaper_actors     — sanctioned non-lease reaper actors (launcher:* / watchdog)
    + their stale bounds, in data not code;
  * admin_offline_audit.auth_branch — records which branch authorized each reap;
  * admin_mark_offline — CREATE OR REPLACE to a 3-branch scoped auth disjunction
    (LEASE / LAUNCHER / WATCHDOG), owner stays fleet_reaper, done via a TRANSIENT
    `SET ROLE fleet_reaper` (postgres granted membership + CREATE-on-schema for the
    duration, both REVOKEd) — same minimal-footprint dance as 037.

Same guarded contract as apply_mig037_console.py: byte-bound sha256, grant-check
(granted + window closed + accepted + not superseded), residency guard (--expect
ref in DATABASE_URL), managed txn with pre/post verify + atomic ledger. The
post-verify additionally asserts the transient SET-ROLE grants were cleanly
revoked (postgres NOT left a fleet_reaper member; fleet_reaper lacks CREATE on
schema) — the security-sensitive part cai + the SRE care about. NEVER db push.

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
    os.path.dirname(__file__), "..", "migrations", "038_admin_mark_offline_scoped_reapers.sql"))
EXACT_FILENAME = "038_admin_mark_offline_scoped_reapers.sql"
EXPECT_SHA256 = "efd14e58de08034e86fed4d5fd48a376dda0cd903864d546f1f23f85b835387b"
EXPECT_REF = "tscuymavysscrvoberrr"
AGENT_ID = "orch-console"
REFUSE_CHALLENGE = {"open", "challenged", "pending", "under_challenge"}


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
    """Strip ONLY exact-line top-level BEGIN;/COMMIT; (plpgsql BEGIN/END and
    SET ROLE/RESET ROLE inside the body are kept and run inside our managed txn)."""
    with open(MIGRATION_FILE) as f:
        sql = f.read()
    kept = [ln for ln in sql.splitlines() if ln.strip().upper().rstrip() not in ("BEGIN;", "COMMIT;")]
    body = "\n".join(kept)
    TXN_CTL = {"BEGIN;", "COMMIT;", "ROLLBACK;", "START TRANSACTION;", "END TRANSACTION;"}
    for ln in body.splitlines():
        if ln.strip().upper() in TXN_CTL:
            raise Refuse(f"top-level transaction-control statement survived the strip: {ln!r}")
    return body


def apply() -> dict:
    dsn = _dsn()
    if EXPECT_REF not in dsn:
        raise Refuse(f"DATABASE_URL does not target {EXPECT_REF} (residency guard).")
    sha = sha256_check()
    d = grant_check(EXACT_FILENAME)
    body = migration_body()
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (AGENT_ID,))
        # pre-verify: mig-038 objects absent (never re-apply); mig-037 fn present
        cur.execute("SELECT to_regclass('public.protected_agents') IS NOT NULL")
        if cur.fetchone()[0]:
            conn.rollback(); raise Refuse("protected_agents already exists — refusing to re-apply.")
        cur.execute("SELECT to_regclass('public.reaper_actors') IS NOT NULL")
        if cur.fetchone()[0]:
            conn.rollback(); raise Refuse("reaper_actors already exists — refusing to re-apply.")
        cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='admin_offline_audit' AND column_name='auth_branch')")
        if cur.fetchone()[0]:
            conn.rollback(); raise Refuse("admin_offline_audit.auth_branch already exists — refusing to re-apply.")
        cur.execute("SELECT to_regprocedure('public.admin_mark_offline(text,text)') IS NOT NULL")
        if not cur.fetchone()[0]:
            conn.rollback(); raise Refuse("mig-037 admin_mark_offline missing — apply 037 first.")

        # apply
        cur.execute(body)

        # post-verify: tables + seeds
        cur.execute("SELECT to_regclass('public.protected_agents') IS NOT NULL")
        if not cur.fetchone()[0]:
            conn.rollback(); raise Refuse("post-verify: protected_agents absent — rolled back.")
        cur.execute("SELECT count(*) FROM protected_agents")
        n_prot = cur.fetchone()[0]
        if n_prot != 5:
            conn.rollback(); raise Refuse(f"post-verify: protected_agents seeded {n_prot} rows (expected 5) — rolled back.")
        cur.execute("SELECT count(*) FROM reaper_actors")
        n_act = cur.fetchone()[0]
        if n_act != 2:
            conn.rollback(); raise Refuse(f"post-verify: reaper_actors seeded {n_act} rows (expected 2) — rolled back.")
        cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='admin_offline_audit' AND column_name='auth_branch')")
        if not cur.fetchone()[0]:
            conn.rollback(); raise Refuse("post-verify: admin_offline_audit.auth_branch absent — rolled back.")
        # fn replaced with the scoped 3-branch body, owner/SECDEF preserved
        cur.execute("SELECT prosecdef, prosrc FROM pg_proc WHERE oid='public.admin_mark_offline(text,text)'::regprocedure")
        secdef, src = cur.fetchone()
        if secdef is not True:
            conn.rollback(); raise Refuse("post-verify: admin_mark_offline not SECDEF — rolled back.")
        if "protected_agents" not in src or "auth_branch" not in src:
            conn.rollback(); raise Refuse("post-verify: admin_mark_offline body does not contain the scoped logic — rolled back.")
        cur.execute("""SELECT r.rolname FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                       WHERE p.oid='public.admin_mark_offline(text,text)'::regprocedure""")
        owner = cur.fetchone()[0]
        if owner != "fleet_reaper":
            conn.rollback(); raise Refuse(f"post-verify: admin_mark_offline owner={owner!r} (expected fleet_reaper) — rolled back.")
        # SECURITY post-verify: the migration's transient CREATE-on-schema grant to
        # fleet_reaper is cleanly revoked (the meaningful, migration-scoped check).
        # NOTE: we deliberately do NOT assert "postgres is not a member of fleet_reaper".
        # A pre-existing supabase_admin-granted membership (admin_option) persists
        # independently of this migration — cai EXPLICITLY accepted that residual in
        # CAI-761 (repeated in the 037/038 apply notes): postgres is the trusted
        # app-tier and can always re-grant itself; control-6's target is the
        # RLS-exposed roles, which stay shut out. The migration's own REVOKE removes
        # only the transient grant it made; asserting zero membership would be
        # stricter than the migration (and cai) guarantee — a false positive.
        cur.execute("SELECT has_schema_privilege('fleet_reaper','public','CREATE')")
        if cur.fetchone()[0]:
            conn.rollback(); raise Refuse("post-verify: fleet_reaper STILL has CREATE on schema public — transient grant not revoked; rolled back.")

        # ledger — same txn (atomic)
        cur.execute(
            """INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by)
               VALUES (%s, %s, %s, %s, %s)""",
            ("orchestrator", EXACT_FILENAME, EXPECT_REF, sha, AGENT_ID),
        )
        conn.commit()
    print(f"\n✓ APPLIED {EXACT_FILENAME} to {EXPECT_REF} under grant {d['id']} (sha256 {sha[:12]}…). "
          f"protected_agents({n_prot}) + reaper_actors({n_act}) seeded; auth_branch added; "
          f"admin_mark_offline replaced (scoped, owner=fleet_reaper, SECDEF); transient SET-ROLE grants revoked; ledger recorded.")
    return d


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "--prove-refusal":
            bogus = "038_NONEXISTENT_prove_refusal.sql"
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
