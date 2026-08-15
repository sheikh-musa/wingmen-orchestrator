"""Apply migration 046 — fleet_proposals, the ideas-up ledger (operator op#13332).

See migrations/046_fleet_proposals.sql and docs/self-improvement-loop-spec.md for the why.
CLAUDE.md/decision-962 forbids `supabase db push` against prod; this is the direct
psycopg-apply. Idempotent.

Usage:
  python scripts/apply_mig046_fleet_proposals.py            # dry-run (rolled back)
  python scripts/apply_mig046_fleet_proposals.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "046_fleet_proposals.sql"


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION.read_text())

        cur.execute("SELECT to_regclass('public.fleet_proposals')")
        if cur.fetchone()[0] is None:
            print("FAILED — fleet_proposals absent after migration", file=sys.stderr)
            conn.rollback()
            return 2

        # The point of the table is that a proposal must contain a suggested change. Prove the
        # CHECK actually rejects a complaint-only row rather than trusting it was written right.
        cur.execute("SAVEPOINT probe")
        try:
            cur.execute("INSERT INTO public.fleet_proposals (from_agent, problem, proposal) "
                        "VALUES ('probe', 'something is wrong', '   ')")
        except psycopg.errors.CheckViolation:
            cur.execute("ROLLBACK TO SAVEPOINT probe")
            print("guard OK — an empty `proposal` is rejected")
        else:
            cur.execute("ROLLBACK TO SAVEPOINT probe")
            print("FAILED — a complaint with no proposal was accepted", file=sys.stderr)
            conn.rollback()
            return 3

        cur.execute("SELECT * FROM public.fleet_proposal_metrics_v")
        cols = [d.name for d in cur.description]
        print("metrics view:", dict(zip(cols, cur.fetchone())))

        if apply:
            conn.commit()
            print("APPLIED (committed)")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back) — re-run with --apply to commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
