#!/usr/bin/env python3
"""apply_120_tabung_report_approvers.py — direct-psycopg apply of ihsanos migration 120.

    supabase/migrations/120_tabung_approval_notifications.sql  ->  goumlynecruxrlmzlntp ONLY

AUTHORITY: cai CAI-RESP-600 approved the SUBSTANCE and armed a grant that would not flip
until ~2026-07-27T01:15Z. The operator overrode that wait explicitly ("apply it now",
op_msg #7333, 2026-07-26 01:41:32Z) after being shown the trade-off in full — the change
is additive, the client is actively blocked, and cai's gate is stop-and-disclose rather
than a veto over the principal. This is recorded, not silent: cai is told it was applied
on the operator's authority.

cai's conditions, carried verbatim:
  1. THAT FILE AND NO OTHER (sha pinned + re-verified at run time).
  2. goumlyne ONLY. ceayj parity was REFUSED as takalluf (no consumer, multi-tenant PROD).
  3. Post-apply proof RAW, per-silo, stating WHICH SILO and WHICH HOST it ran from.

NEVER `supabase db push` (CLAUDE.md / decision 962).

    .venv/bin/python3 scripts/apply_120_tabung_report_approvers.py --dry-run
    .venv/bin/python3 scripts/apply_120_tabung_report_approvers.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ORCH = Path(__file__).resolve().parent.parent
load_dotenv(ORCH / ".env")

EXPECT_REF = "goumlynecruxrlmzlntp"
EXPECT_SHA = "45a168d01c156bee009c4c613f9d955ae9a4ac7f"  # git blob sha on origin/main
MIG = "supabase/migrations/120_tabung_approval_notifications.sql"
IHSANOS = Path.home() / "wingmen" / "projects" / "ihsanos"

# Fingerprints that must / must not be present. Residency is asserted against the DATA,
# not just the connection string — a DSN can be edited, a tenant roster cannot be faked.
MUST_HAVE_ORG = "Madrasah Irsyad Zuhri Al-Islamiah"


def die(msg: str) -> None:
    print(f"\n  ✗ REFUSED — {msg}")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not (a.apply or a.dry_run):
        die("pass --dry-run or --apply")

    host = socket.gethostname()
    print(f"  host        : {host}")
    print(f"  target ref  : {EXPECT_REF}")

    dsn = os.environ.get("GOUMLYNE_DATABASE_URL")
    if not dsn:
        die("GOUMLYNE_DATABASE_URL absent")

    # ---- GATE 1: the DSN must name the expected silo -------------------------
    if EXPECT_REF not in dsn:
        die(f"DSN does not name {EXPECT_REF} — refusing to guess which silo this is")

    # ---- GATE 2: the FILE must be byte-identical to what cai reviewed --------
    blob = subprocess.run(
        ["git", "rev-parse", f"origin/main:{MIG}"],
        cwd=IHSANOS, capture_output=True, text=True,
    ).stdout.strip()
    if blob != EXPECT_SHA:
        die(f"migration blob sha {blob or '<none>'} != reviewed {EXPECT_SHA}")
    sql = subprocess.run(
        ["git", "show", f"origin/main:{MIG}"],
        cwd=IHSANOS, capture_output=True, text=True,
    ).stdout
    if not sql.strip():
        die("migration file read back empty")
    print(f"  file sha    : {blob}  ✓ matches reviewed")
    print(f"  file bytes  : {len(sql)}  sha256={hashlib.sha256(sql.encode()).hexdigest()[:16]}…")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # ---- GATE 3: residency asserted against the DATA -----------------
            cur.execute("SELECT count(*) FROM organizations WHERE name = %s", (MUST_HAVE_ORG,))
            if cur.fetchone()[0] != 1:
                die(f"'{MUST_HAVE_ORG}' not found — this is NOT the irsyad silo")
            cur.execute("SELECT count(*) FROM organizations")
            n_orgs = cur.fetchone()[0]
            if n_orgs > 5:
                die(f"{n_orgs} orgs present — looks like a MULTI-TENANT silo, not goumlyne")
            print(f"  residency   : ✓ irsyad org present, {n_orgs} orgs total (single-tenant shape)")

            # ---- PRE-STATE ---------------------------------------------------
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='tabung_report_approvers'"
            )
            pre = cur.fetchone()[0]
            print(f"  pre-state   : tabung_report_approvers {'EXISTS' if pre else 'ABSENT'}")

            if a.dry_run:
                print("\n  DRY RUN — nothing applied. All gates passed.")
                return

            # ---- APPLY -------------------------------------------------------
            cur.execute(sql)
        conn.commit()

    # ---- POST-APPLY PROOF, RAW ----------------------------------------------
    print("\n  ── POST-APPLY PROOF ──")
    print(f"  silo={EXPECT_REF}  host={host}")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='tabung_report_approvers'"
        )
        print(f"  table present            : {cur.fetchone()[0]}")
        cur.execute(
            "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name='tabung_report_approvers' AND grantee IN ('anon','authenticated','service_role') "
            "ORDER BY grantee, privilege_type"
        )
        rows = cur.fetchall()
        print(f"  grants                   : {rows if rows else '(none)'}")
        cur.execute(
            "SELECT has_table_privilege('anon','public.tabung_report_approvers','INSERT'), "
            "       has_table_privilege('anon','public.tabung_report_approvers','SELECT')"
        )
        print(f"  has_table_privilege(anon) : INSERT/SELECT = {cur.fetchone()}")
        cur.execute("SELECT count(*) FROM public.tabung_report_approvers")
        print(f"  rows                     : {cur.fetchone()[0]}")
    print("\n  ✓ applied. Approver rows must now be created THROUGH THE UI PATH (cai condition 2).")


if __name__ == "__main__":
    main()
