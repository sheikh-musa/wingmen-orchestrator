#!/usr/bin/env python3
"""apply_mig037_console.py — ORCH-CONSOLE guarded §6.6 apply of mig-037.

Executor picked by cai CAI-RESP-761 (decision 2066): the BUILDER (cc-fleet-health)
must NOT apply its own migration (build!=apply separation, per mig-148). orch-console
applies migrations/037_admin_mark_offline.sql — the narrow SECDEF admin_mark_offline()
reap capability + the enforce_agent_status_identity SECDEF->INVOKER change cai blessed.

Differences from apply_mig148_console.py (deliberate, mig-037-specific):
  1. BYTE-BOUND grant: the grant (decision 2066) is bound to the file's sha256
     (d09150c6...c42a8b36), not just its name. We REFUSE if the file's live sha256
     drifts from the granted digest — stronger than mig-148's filename bind.
  2. SAME DB for grant-check AND apply: mig-037 targets the substrate/bus silo
     (tscuymavysscrvoberrr) which is DATABASE_URL — the very store that holds
     strategic_decisions. One DSN; residency guard asserts the ref is in it.
  3. FUNCTION-HEAVY file: mig-148's belt-and-suspenders BEGIN-strip falsely trips on
     plpgsql `BEGIN` lines inside DO/function bodies. Here we strip ONLY exact-line
     top-level `BEGIN;`/`COMMIT;` and assert no top-level txn-control survives —
     bare plpgsql BEGIN/END are left untouched.

Contract (cai CAI-RESP-761 / decision 2066): REFUSE unless a strategic_decision NAMES
the exact file with execution_status='granted' + challenge window CLOSED + not
superseded + challenge_status not open, AND the file's sha256 == the granted digest.
MANAGED txn with pre/post verify; the ledger row is written in the SAME txn (atomic
with the migration). NEVER supabase db push.

Modes:
  --prove-refusal   prove the gate REFUSES for an ungranted (bogus) filename, then exit 0
  --check-only      run the grant-check + sha256 check for the REAL file, do NOT apply
  (default)         grant-check + sha256, then apply in a managed txn with pre/post verify + ledger

Env: DATABASE_URL (substrate/bus silo tscuymavysscrvoberrr — decisions AND target)
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone

import psycopg

MIGRATION_FILE = os.path.join(os.path.dirname(__file__), "..", "migrations", "037_admin_mark_offline.sql")
MIGRATION_FILE = os.path.abspath(MIGRATION_FILE)
EXACT_FILENAME = "037_admin_mark_offline.sql"
EXPECT_SHA256 = "d09150c664cd8e796ee5ad72e0b5226645a3ff260117086d67e0bc9ec42a8b36"
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
    """The grant is byte-bound. REFUSE if the file drifted from the granted digest."""
    got = file_sha256()
    if got != EXPECT_SHA256:
        raise Refuse(f"sha256 DRIFT: file={got} != granted {EXPECT_SHA256} — grant is byte-bound; REFUSING.")
    print(f"✓ sha256 byte-bound check PASSED: {got}")
    return got


def grant_check(filename: str) -> dict:
    """Return the granted decision row that NAMES `filename`, or raise Refuse."""
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
    """DDL with the file's own OUTER BEGIN;/COMMIT; stripped — the applier owns the
    txn boundary. Only exact-line top-level `BEGIN;`/`COMMIT;` are removed; plpgsql
    `BEGIN`/`END` inside DO/function bodies (no trailing semicolon on BEGIN) are kept."""
    with open(MIGRATION_FILE) as f:
        sql = f.read()
    kept = [ln for ln in sql.splitlines() if ln.strip().upper().rstrip() not in ("BEGIN;", "COMMIT;")]
    body = "\n".join(kept)
    # correctness: no TOP-LEVEL txn-control statement may survive (a bare plpgsql
    # BEGIN has no semicolon, so it is not caught here — exactly what we want).
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
        # pre-verify: none of the new objects exist yet (never re-apply)
        cur.execute("SELECT to_regprocedure('public.admin_mark_offline(text,text)') IS NOT NULL")
        if cur.fetchone()[0]:
            conn.rollback(); raise Refuse("admin_mark_offline already exists — refusing to re-apply.")
        cur.execute("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fleet_reaper')")
        if cur.fetchone()[0]:
            conn.rollback(); raise Refuse("fleet_reaper role already exists — refusing to re-apply.")
        cur.execute("SELECT to_regclass('public.admin_offline_audit') IS NOT NULL")
        if cur.fetchone()[0]:
            conn.rollback(); raise Refuse("admin_offline_audit already exists — refusing to re-apply.")
        # pre-verify: base guard is SECDEF today (we are about to flip it to INVOKER)
        cur.execute("SELECT prosecdef FROM pg_proc WHERE proname='enforce_agent_status_identity'")
        row = cur.fetchone()
        if not row:
            conn.rollback(); raise Refuse("enforce_agent_status_identity missing pre-apply — unexpected.")
        if row[0] is not True:
            conn.rollback(); raise Refuse(f"enforce_agent_status_identity prosecdef={row[0]} pre-apply (expected True/DEFINER).")

        # apply
        cur.execute(body)

        # post-verify: all three objects present
        cur.execute("SELECT to_regprocedure('public.admin_mark_offline(text,text)') IS NOT NULL")
        if not cur.fetchone()[0]:
            conn.rollback(); raise Refuse("post-verify: admin_mark_offline absent after apply — rolled back.")
        cur.execute("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='fleet_reaper')")
        if not cur.fetchone()[0]:
            conn.rollback(); raise Refuse("post-verify: fleet_reaper role absent after apply — rolled back.")
        cur.execute("SELECT to_regclass('public.admin_offline_audit') IS NOT NULL")
        if not cur.fetchone()[0]:
            conn.rollback(); raise Refuse("post-verify: admin_offline_audit absent after apply — rolled back.")
        # post-verify: enforce guard is now INVOKER (the blessed SECDEF->INVOKER flip)
        cur.execute("SELECT prosecdef FROM pg_proc WHERE proname='enforce_agent_status_identity'")
        if cur.fetchone()[0] is not False:
            conn.rollback(); raise Refuse("post-verify: enforce_agent_status_identity still SECDEF — flip did not take, rolled back.")
        # post-verify: fn owner is fleet_reaper (the unforgeable definer)
        cur.execute("""SELECT r.rolname FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                       WHERE p.oid='public.admin_mark_offline(text,text)'::regprocedure""")
        owner = cur.fetchone()[0]
        if owner != "fleet_reaper":
            conn.rollback(); raise Refuse(f"post-verify: admin_mark_offline owner={owner!r} (expected fleet_reaper) — rolled back.")

        # ledger — SAME txn (atomic with the migration)
        cur.execute(
            """INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by)
               VALUES (%s, %s, %s, %s, %s)""",
            ("orchestrator", EXACT_FILENAME, EXPECT_REF, sha, AGENT_ID),
        )
        conn.commit()
    print(f"\n✓ APPLIED {EXACT_FILENAME} to {EXPECT_REF} under grant {d['id']} (sha256 {sha[:12]}…). "
          f"admin_mark_offline (owner=fleet_reaper) + fleet_reaper role + admin_offline_audit present; "
          f"enforce_agent_status_identity now INVOKER; ledger recorded.")
    return d


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "--prove-refusal":
            bogus = "037_NONEXISTENT_prove_refusal.sql"
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
