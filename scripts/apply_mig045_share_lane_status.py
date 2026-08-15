"""Apply migration 045 — live client-safe lane/pool status views for share.wingmen.dev.

op#13342 ("the gazzabyte status needs to show realtime lane data. its so stale"). See the
migration header for the why. CLAUDE.md/decision-962 forbids `supabase db push` against
prod — this is the direct psycopg-apply. Idempotent (IF NOT EXISTS / CREATE OR REPLACE /
ON CONFLICT DO UPDATE).

The share_readonly role's PASSWORD is never in the migration or in git. Pass it here; the
script sets it and prints the DSN line to paste into the share host's Vercel env. Re-running
with a new password rotates it.

Usage:
  python scripts/apply_mig045_share_lane_status.py                       # dry-run (rolled back)
  python scripts/apply_mig045_share_lane_status.py --apply               # commit, keep password
  python scripts/apply_mig045_share_lane_status.py --apply --set-password  # commit + rotate password
"""
from __future__ import annotations

import os
import pathlib
import secrets
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "045_share_lane_status.sql"

# The reader role must be able to SELECT these and NOTHING else.
EXPECTED_GRANTS = {"share_lane_status_v", "share_pool_status_v"}


def _readable_by_share_readonly(cur) -> set:
    cur.execute(
        """SELECT table_name FROM information_schema.role_table_grants
           WHERE grantee = 'share_readonly' AND table_schema = 'public'"""
    )
    return {r[0] for r in cur.fetchall()}


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    rotate = "--set-password" in sys.argv
    sql = MIGRATION.read_text()

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        # Preconditions: the live telemetry this view reads must actually exist.
        for rel in ("public.pane_context", "public.pool_usage"):
            cur.execute("SELECT to_regclass(%s)", (rel,))
            if cur.fetchone()[0] is None:
                print(f"FAILED — {rel} absent; nothing to derive from", file=sys.stderr)
                return 2

        cur.execute(sql)

        password = None
        if rotate:
            password = secrets.token_urlsafe(32)
            # psycopg cannot parameterise a role name/password in ALTER ROLE; the value is
            # locally generated (never user input), and quote_literal keeps it well-formed.
            cur.execute("SELECT quote_literal(%s)", (password,))
            lit = cur.fetchone()[0]
            cur.execute(f"ALTER ROLE share_readonly WITH LOGIN PASSWORD {lit}")

        # Verify the privilege surface is EXACTLY the two views — a reader role that can
        # read anything else is a finding, not a detail.
        readable = _readable_by_share_readonly(cur)
        if readable != EXPECTED_GRANTS:
            print(f"FAILED — share_readonly reads {sorted(readable)}, expected "
                  f"{sorted(EXPECTED_GRANTS)}", file=sys.stderr)
            conn.rollback()
            return 3

        # Verify the views actually return live rows for the seeded tenant.
        cur.execute("SELECT label, state, context_pct FROM public.share_lane_status_v "
                    "WHERE tenant='gazzabyte' ORDER BY sort")
        lanes = cur.fetchall()
        cur.execute("SELECT used_pct, projected_pct, resets_at FROM public.share_pool_status_v "
                    "WHERE tenant='gazzabyte'")
        pool = cur.fetchone()
        print(f"share_lane_status_v(gazzabyte): {len(lanes)} lanes")
        for row in lanes:
            print("   ", row)
        print("share_pool_status_v(gazzabyte):", pool)
        if not lanes:
            print("FAILED — no lanes visible for gazzabyte after seed", file=sys.stderr)
            conn.rollback()
            return 4
        if pool is None:
            print("FAILED — no pool row for gazzabyte (pool_usage missing that pool?)",
                  file=sys.stderr)
            conn.rollback()
            return 5

        if apply:
            conn.commit()
            print("APPLIED (committed)")
            if password:
                host = "aws-0-ap-southeast-1.pooler.supabase.com"  # informational only
                print("\nshare_readonly password ROTATED. Set on the share host (Vercel):")
                print(f"  SHARE_STATUS_DB_URL=postgresql://share_readonly:{password}@<host>:<port>/postgres")
                print(f"  (host/port/db: copy from DATABASE_URL in .env; example host {host})")
                print("  Do NOT commit this value.")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back) — re-run with --apply to commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
