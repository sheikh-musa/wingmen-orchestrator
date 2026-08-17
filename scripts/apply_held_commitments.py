"""Apply migration 051 — held_commitments (CAI-RESP-1029 build 2/2, op#13770/13776/13788).

CLAUDE.md forbids `supabase db push` against prod (decision-962): the CLI's shadow-diff
path re-applies historic CREATE OR REPLACE VIEW statements and silently strips later arms.
Use this direct psycopg-apply instead.

Idempotent: CREATE TABLE/INDEX IF NOT EXISTS + CREATE OR REPLACE VIEW, so re-running is safe.
No seed — commitments are written by the bodies that make them.

Dry-run is the DEFAULT and it genuinely rolls back; the post-checks below run inside the same
transaction either way, so a dry-run proves the DDL and the constraints before anything commits.

Usage:
  python scripts/apply_held_commitments.py            # dry-run (rolled back)
  python scripts/apply_held_commitments.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "051_held_commitments.sql"


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv

    # The migration file wraps itself in BEGIN/COMMIT. Strip those so THIS script owns the
    # transaction boundary — otherwise the file's COMMIT fires mid-way and a dry-run would
    # commit anyway, which is the exact hazard that let a repro suite damage production on
    # 2026-08-16 (CAI-RESP-1017: setup UPDATEs landed below the final ROLLBACK and ran in
    # autocommit). A dry-run flag that does not actually disarm the write is decorative.
    sql = MIGRATION.read_text()
    stripped = "\n".join(
        line for line in sql.splitlines()
        if line.strip().upper() not in ("BEGIN;", "COMMIT;")
    )
    if "BEGIN;" in sql and "BEGIN;" in stripped:
        print("FAIL: could not strip BEGIN/COMMIT — refusing to run", file=sys.stderr)
        return 1

    with psycopg.connect(dsn, connect_timeout=20, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(stripped)

            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='held_commitments' ORDER BY ordinal_position")
            cols = [r[0] for r in cur.fetchall()]
            print("held_commitments columns:", cols)

            cur.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid='held_commitments'::regclass AND contype='c' ORDER BY conname")
            checks = [r[0] for r in cur.fetchall()]
            print("check constraints:", checks)

            cur.execute("SELECT count(*) FROM held_commitments_due")
            print("held_commitments_due rows (expect 0 on a fresh table):", cur.fetchone()[0])

            # ── POST-CONDITIONS. These assert MY OWN effect, not world-state: on a live bus any
            # post-condition phrased as "nothing else changed" is tripped by other bodies simply
            # working (the sla-watchdog rollback, 2026-08-16). They fail SAFE — a spurious
            # rollback, never a false green.
            failures: list[str] = []

            for required in ("owner_agent", "due_at", "status", "fired_at",
                             "discharged_at", "discharged_by"):
                if required not in cols:
                    failures.append(f"missing column {required}")

            # The distinction the whole table exists to protect: fired is a machine event,
            # discharged is a claim about the world. If these ever collapse into one column the
            # table can report a promise kept because a trigger shouted into a dead pane.
            if "fired_at" in cols and "discharged_at" in cols:
                pass
            else:
                failures.append("fired_at/discharged_at must both exist and stay separate")

            for required_check in ("held_commitments_discharge_is_attributable",
                                   "held_commitments_cancel_has_reason",
                                   "held_commitments_fired_is_stamped",
                                   "held_commitments_status_check"):
                if required_check not in checks:
                    failures.append(f"missing check constraint {required_check}")

            # Prove the attributability guard actually BITES rather than merely existing — a
            # constraint nobody has seen reject anything is an unexercised control, and this
            # fleet has shipped two of those (047's unreachable EXERCISED state, the 054/056
            # check that returned zero). Exercise it inside a savepoint.
            cur.execute("SAVEPOINT probe")
            try:
                cur.execute(
                    "INSERT INTO held_commitments "
                    "(owner_agent,title,due_at,status,created_by) "
                    "VALUES ('probe','probe',now(),'discharged','probe')")
                failures.append(
                    "discharge-attributability check DID NOT FIRE — a discharge with no "
                    "discharged_by was accepted")
                cur.execute("ROLLBACK TO SAVEPOINT probe")
            except psycopg.errors.CheckViolation:
                cur.execute("ROLLBACK TO SAVEPOINT probe")
                print("probe: discharge without discharged_by correctly REJECTED")

            # And prove a legitimate write still works — a guard that blocks everything is not a
            # guard, it is an outage. Negative control for the probe above.
            cur.execute("SAVEPOINT probe2")
            try:
                cur.execute(
                    "INSERT INTO held_commitments "
                    "(owner_agent,title,due_at,status,created_by) "
                    "VALUES ('probe','probe',now(),'pending','probe')")
                print("probe: a legitimate pending commitment correctly ACCEPTED")
            except Exception as exc:  # noqa: BLE001 - report, don't mask
                failures.append(f"legitimate pending insert was REJECTED: {exc}")
            cur.execute("ROLLBACK TO SAVEPOINT probe2")

            if failures:
                print("\nPOST-CONDITIONS FAILED — rolling back:", file=sys.stderr)
                for f in failures:
                    print("  -", f, file=sys.stderr)
                conn.rollback()
                return 1
            print("\npost-conditions: PASS")

        if apply:
            conn.commit()
            print("APPLIED (committed)")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back) — re-run with --apply to commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
