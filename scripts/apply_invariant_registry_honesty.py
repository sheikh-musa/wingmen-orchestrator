#!/usr/bin/env python3
"""Apply migrations/047_invariant_registry_honesty.sql via DIRECT psycopg.

NEVER `supabase db push` (decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001): the CLI's shadow-diff
path re-applies historic CREATE OR REPLACE VIEW bodies from older migrations and silently strips
later arms. This is the orch's standard direct-apply pattern (PR #41/#42/#44).

Target: the SUBSTRATE coordination-plane DB (DATABASE_URL), NOT any client silo.

Additive and reversible: creates one view + two COMMENTs. Touches no data, no columns, no
constraints. Applied in ONE managed transaction; verifies AFTER apply and rolls back if the
post-condition fails, so a half-applied honesty fix can never be reported as done.

Usage:  scripts/apply_invariant_registry_honesty.py [--dry-run]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ORCH_DIR = Path(__file__).resolve().parent.parent
SQL_PATH = ORCH_DIR / "migrations" / "047_invariant_registry_honesty.sql"


def main() -> int:
    load_dotenv(ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL not set", file=sys.stderr)
        return 2

    sql = SQL_PATH.read_text()
    if "--dry-run" in sys.argv:
        print(f"dry-run: would apply {SQL_PATH.name} ({len(sql)} bytes) to the substrate DB")
        return 0

    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql)

            # POST-CONDITIONS, checked inside the same transaction, rolled back on failure.
            #
            # cc-quality F2 (PR #74 review) — REWRITTEN. The original guard asserted "every row
            # reads UNEXERCISED and none is fresh". That was TRUE today but it asserted a
            # TRANSIENT FACT ("no sink is wired yet"), not the view's invariant. It would have
            # become a ROLLBACK LANDMINE: the moment the CAI-985 A3 sink legitimately exercises
            # RESIDENCY-1, re-running this re-runnable migration would fail and roll back a
            # genuinely-exercised state -- reading CORRECT coverage as a failure. Exactly the
            # never-green defect, inverted. The guards below assert what must hold FOREVER, so
            # they keep catching regressions after a sink exists instead of blocking one.
            #
            # (1) Nothing may render as coverage without a live measurer. This IS the doctrine
            #     (CAI-RESP-986 §1) expressed as an executable assertion.
            cur.execute(
                "SELECT count(*) FROM invariant_registry_state "
                "WHERE is_exercised_fresh "
                "  AND (gate_status IS NULL "
                "       OR upper(gate_status) <> 'COVERED')"
            )
            (false_coverage,) = cur.fetchone()
            if false_coverage:
                raise SystemExit(
                    f"post-condition FAILED: {false_coverage} row(s) report is_exercised_fresh "
                    "WITHOUT a live measurer. Rolling back — an honesty view that reports "
                    "coverage is worse than none."
                )

            # (2) The label and the boolean must never disagree. cc-quality PROVED they cannot
            #     today (54-cell matrix, case-exhaustive), and F1 single-sourced the two terms
            #     that could have drifted them apart. This asserts that guarantee at every apply,
            #     so a future edit to one arm alone cannot ship: a board rendering green off the
            #     boolean while the label says UNEXERCISED would be this migration's own
            #     counterexample.
            cur.execute(
                "SELECT count(*) FROM invariant_registry_state "
                "WHERE is_exercised_fresh <> (exercise_state = 'EXERCISED')"
            )
            (disagreements,) = cur.fetchone()
            if disagreements:
                raise SystemExit(
                    f"post-condition FAILED: {disagreements} row(s) where is_exercised_fresh "
                    "disagrees with exercise_state='EXERCISED'. Rolling back — the boolean and "
                    "the label have drifted apart."
                )

            # Informational only — NEVER asserted. The distribution is a transient fact about how
            # much has been wired so far; asserting it is what F2 was.
            cur.execute(
                "SELECT count(*), "
                "count(*) FILTER (WHERE exercise_state = 'UNEXERCISED'), "
                "count(*) FILTER (WHERE is_exercised_fresh), "
                "count(*) FILTER (WHERE unexercised_kind = 'NEVER'), "
                "count(*) FILTER (WHERE unexercised_kind = 'MANUALLY-ATTESTED') "
                "FROM invariant_registry_state"
            )
            total, unexercised, fresh, never, attested = cur.fetchone()
            print(
                f"invariant_registry_state: {total} rows, {unexercised} UNEXERCISED "
                f"({never} NEVER / {attested} MANUALLY-ATTESTED), {fresh} exercised-fresh"
            )
            print("post-conditions OK: no coverage without a live measurer; label and boolean agree")
        print("applied 047_invariant_registry_honesty.sql")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
