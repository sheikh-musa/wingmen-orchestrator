#!/usr/bin/env python3
"""apply_mig148_console.py — ORCH-CONSOLE guarded §6.6 apply of mig-148.

Executor picked by cai CAI-RESP-756 (decision 2060): the BUILDER (cc-irsyad-b) must
NOT apply its own client-silo migration (build!=apply separation, CAI-753 principle),
and orch-console is alive + off the stalled VPS hub. This mirrors cc-irsyad-b's
reviewed apply-mig-148.py (the "proven apply-143 grant-check"), with two hardening
fixes from that review (post-verify the INDEX; schema-robust FK compare) and a
--prove-refusal mode (cai's precondition: prove the gate refuses-without-grant).

Grant-check reads strategic_decisions via DIRECT psycopg on DATABASE_URL (the
substrate store cai writes the grant to) — not REST — to avoid the REST
read-after-write lag right after a grant lands.

Contract (cai #16279 / CAI-756): REFUSE unless a strategic_decision NAMES the exact
file with execution_status='granted' + challenge window CLOSED + not superseded +
challenge_status not open. Residency guard (--expect-ref). MANAGED txn that STRIPS
the file's own BEGIN/COMMIT (the wet-prove-incident root cause). NEVER supabase db push.

Modes:
  --prove-refusal   prove the gate REFUSES for an ungranted (bogus) filename, then exit 0
  --check-only      run the grant-check for the REAL file, report PASS/REFUSE, do NOT apply
  (default)         grant-check the real file, then apply in a managed txn with pre/post verify

Env: DATABASE_URL (decisions ledger), GOUMLYNE_DATABASE_URL (target; ref must be goumlynecruxrlmzlntp)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import psycopg

MIGRATION_FILE = os.path.expanduser(
    "~/wingmen/projects/ihsanos-irsyad.wt-prog1/supabase/migrations/148_wc_ingest_donation_category.sql"
)
EXACT_FILENAME = "148_wc_ingest_donation_category.sql"
EXPECT_REF = "goumlynecruxrlmzlntp"
AGENT_ID = "orch-console"
TABLE = "wc_order_ingest_items"
COLUMN = "donation_category_id"
INDEX = "idx_wc_ingest_items_category"
REFUSE_CHALLENGE = {"open", "challenged", "pending", "under_challenge"}


class Refuse(Exception):
    pass


def _decisions_dsn() -> str:
    return os.environ.get("DATABASE_URL") or os.environ["SUPABASE_DB_URL"]


def grant_check(filename: str) -> dict:
    """Return the granted decision row that NAMES `filename`, or raise Refuse."""
    with psycopg.connect(_decisions_dsn()) as conn, conn.cursor() as cur:
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
    """DDL with the file's own BEGIN;/COMMIT; stripped — the applier owns the txn
    boundary (running the file's COMMIT inside our txn is what caused the incident)."""
    with open(MIGRATION_FILE) as f:
        sql = f.read()
    kept = [ln for ln in sql.splitlines() if ln.strip().upper().rstrip() not in ("BEGIN;", "COMMIT;")]
    body = "\n".join(kept)
    # belt-and-suspenders: no stray transaction-control left in the body
    for banned in ("BEGIN;", "COMMIT;", "BEGIN ", "COMMIT ", "ROLLBACK"):
        for ln in body.splitlines():
            if ln.strip().upper().startswith(banned.upper().strip() if banned.endswith(" ") else banned):
                if ln.strip().upper().rstrip(";") in ("BEGIN", "COMMIT", "ROLLBACK"):
                    raise Refuse(f"transaction-control statement survived the strip: {ln!r}")
    return body


def _fk_target(cur) -> str | None:
    cur.execute(
        """SELECT confrelid::regclass::text FROM pg_constraint
           WHERE contype='f' AND conrelid=%s::regclass
             AND array_position(conkey,(SELECT attnum FROM pg_attribute
                 WHERE attrelid=%s::regclass AND attname=%s)) IS NOT NULL""",
        (f"public.{TABLE}", f"public.{TABLE}", COLUMN),
    )
    row = cur.fetchone()
    return row[0] if row else None


def apply() -> dict:
    dsn = os.environ.get("GOUMLYNE_DATABASE_URL", "")
    if EXPECT_REF not in dsn:
        raise Refuse(f"GOUMLYNE_DATABASE_URL does not target {EXPECT_REF} (residency guard).")
    d = grant_check(EXACT_FILENAME)
    body = migration_body()
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (AGENT_ID,))
        # pre-verify: column absent
        cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name=%s AND column_name=%s)", (TABLE, COLUMN))
        if cur.fetchone()[0]:
            conn.rollback()
            raise Refuse(f"{TABLE}.{COLUMN} already present — refusing to re-apply.")
        # apply
        cur.execute(body)
        # post-verify: column present
        cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name=%s AND column_name=%s)", (TABLE, COLUMN))
        if not cur.fetchone()[0]:
            conn.rollback()
            raise Refuse("post-verify: column absent after apply — rolled back.")
        # post-verify: FK -> donation_categories (schema-robust: accept qualified or bare)
        fk = _fk_target(cur)
        if not fk or fk.split(".")[-1] != "donation_categories":
            conn.rollback()
            raise Refuse(f"post-verify: FK target={fk!r} (expected donation_categories) — rolled back.")
        # post-verify: INDEX present (hardening #1 from the review)
        cur.execute("SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE tablename=%s AND indexname=%s)",
                    (TABLE, INDEX))
        if not cur.fetchone()[0]:
            conn.rollback()
            raise Refuse(f"post-verify: index {INDEX} absent after apply — rolled back.")
        # sanity: table still empty (column-first, no data touched)
        cur.execute(f"SELECT count(*) FROM {TABLE}")
        rows = cur.fetchone()[0]
        conn.commit()
    print(f"\n✓ APPLIED {EXACT_FILENAME} to {EXPECT_REF} under grant {d['id']}. "
          f"column+FK(donation_categories)+index verified; {TABLE} row count={rows}.")
    return d


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "--prove-refusal":
            bogus = "148_NONEXISTENT_prove_refusal.sql"
            try:
                grant_check(bogus)
            except Refuse as e:
                print(f"✓ PROVE-REFUSAL PASSED: gate correctly REFUSED an ungranted file — {e}")
                return 0
            print("✗ PROVE-REFUSAL FAILED: gate did NOT refuse a bogus filename — DO NOT TRUST.")
            return 3
        elif mode == "--check-only":
            grant_check(EXACT_FILENAME)
            print("(check-only: grant is valid; NOT applying.)")
            return 0
        else:
            apply()
            return 0
    except Refuse as e:
        print(f"\n✗ REFUSE (did not apply): {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
