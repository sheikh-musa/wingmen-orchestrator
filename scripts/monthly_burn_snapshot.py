#!/usr/bin/env python3
"""monthly_burn_snapshot.py — fleet monthly burn tracker (Musa op#29595,
relayed Nazim agent_messages 65284/65369, 2026-10-13: "just ask finance to
keep track of our monthly burn. add the glm costs as well.")

Reads finance_subscriptions + the finance_burn view (substrate DB), renders a
plain-text snapshot, writes the full version to
~/wingmen/projects/finance/ledger/reports/monthly-burn-<YYYY-MM>.md, and posts
a short summary as a bus row (agent_messages, from_agent='cc-finance',
to_agent='orch-console') — per Nazim's ruling, this goes to HIM, never
directly to Musa; he relays what's worth surfacing.

Deliberately simple, same shape as scripts/asks_daily_digest.py: at most one
post per calendar month (a persisted month-stamp in
$ORCH/logs/monthly_burn_snapshot_state.json is the only dedup), so a launchd
misfire or manual re-run can never double-post by construction.

Scheduled via launchd/dev.wingmen.cc-finance-monthly-burn.plist, 1st of month
01:00 UTC = 09:00 host-local (this Mac Mini runs Asia/Singapore, UTC+8) —
see that file's comment for the host-TZ conversion. Landing this file does
NOT wire it into launchd; a plist must still be separately `launchctl load`ed
at go-live (per Nazim: he loads it, this PR does not).

Usage:
  monthly_burn_snapshot.py [--dry-run] [--force]
    --dry-run   print the snapshot, write nothing, post nothing, stamp nothing
    --force     post even if already posted this month (manual re-run / testing)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# Hardcoded to the real orchestrator checkout (not relative to this file) —
# same convention as asks_daily_digest.py: this script may be developed in a
# worktree, but at runtime it must always resolve the live .env/state, which
# only exists in the real checkout.
ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
FINANCE_REPO = Path(os.path.expanduser("~/wingmen/projects/finance"))
STATE_FILE = ORCH / "logs" / "monthly_burn_snapshot_state.json"

load_dotenv(ORCH / ".env")


def _dsn() -> "str | None":
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def fetch_subscriptions(conn) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, vendor, category, plan, amount_sgd_month, status, "
            "       owner, confirmed, updated_at "
            "FROM finance_subscriptions ORDER BY category, name"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_burn_totals(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT active_burn, active_burn_confirmed, upcoming_burn, unconfirmed_rows "
                     "FROM finance_burn")
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else {}


def render_full_report(rows: list, totals: dict, month: str) -> str:
    """The full per-line markdown report, written to ledger/reports/."""
    lines = [f"# Fleet monthly burn — {month} (auto-snapshot)", ""]
    lines.append(f"Active burn: S${totals.get('active_burn', 0)}/mo "
                 f"(S${totals.get('active_burn_confirmed', 0)} confirmed, "
                 f"{totals.get('unconfirmed_rows', 0)} unconfirmed rows). "
                 f"Upcoming: S${totals.get('upcoming_burn', 0)}/mo.")
    lines.append("")
    lines.append("| Name | Vendor | Category | Status | S$/mo | Confirmed | Owner |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(f"| {r['name']} | {r['vendor']} | {r['category']} | {r['status']} | "
                     f"{r['amount_sgd_month']} | {'yes' if r['confirmed'] else 'NO'} | {r['owner']} |")
    lines.append("")
    lines.append("Full line-by-line sourcing/notes live in finance_subscriptions.notes (substrate DB), "
                 "not duplicated here. See ledger/reports/monthly-burn-2026-10.md for the first hand-built "
                 "version's methodology and open-gap list — this auto-snapshot is the numbers only.")
    return "\n".join(lines)


def render_bus_summary(rows: list, totals: dict, month: str) -> str:
    """Short version for the agent_messages bus row — Nazim relays what matters."""
    unconfirmed = [r for r in rows if not r["confirmed"] and r["status"] == "active"]
    lines = [
        f"📊 Monthly burn snapshot ({month}): S${totals.get('active_burn', 0)}/mo active "
        f"(S${totals.get('active_burn_confirmed', 0)} confirmed, "
        f"{totals.get('unconfirmed_rows', 0)} unconfirmed rows)."
    ]
    if unconfirmed:
        names = ", ".join(r["name"] for r in unconfirmed)
        lines.append(f"Still unconfirmed: {names}.")
    lines.append(f"Full table: projects/finance/ledger/reports/monthly-burn-{month}.md")
    return " ".join(lines)


def _already_posted_this_month(month: str) -> bool:
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        return False
    return state.get("last_posted_month") == month


def _mark_posted(month: str) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"last_posted_month": month}))
    except Exception:
        pass


def post_bus_row(conn, summary: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages "
            "(from_agent, to_agent, message_type, subject, body, requires_response, priority, posted_by_identity) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            ("cc-finance", "orch-console", "update",
             "Monthly burn snapshot (automated, op#29595)", summary, False, "P3", "cc-finance"),
        )
    conn.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                     help="print the snapshot, write/post/stamp nothing")
    ap.add_argument("--force", action="store_true",
                     help="post even if already posted this month (manual re-run)")
    args = ap.parse_args(argv)

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    if not args.force and not args.dry_run and _already_posted_this_month(month):
        print(f"monthly_burn_snapshot: already posted for {month} — skipping (use --force to repost)")
        return 0

    dsn = _dsn()
    if not dsn:
        print("monthly_burn_snapshot: no DATABASE_URL/SUPABASE_DB_URL", file=sys.stderr)
        return 2

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        rows = fetch_subscriptions(conn)
        totals = fetch_burn_totals(conn)

        if args.dry_run:
            print(render_full_report(rows, totals, month))
            print()
            print("--- bus summary would be ---")
            print(render_bus_summary(rows, totals, month))
            return 0

        report_path = FINANCE_REPO / "ledger" / "reports" / f"monthly-burn-{month}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_full_report(rows, totals, month))

        summary = render_bus_summary(rows, totals, month)
        post_bus_row(conn, summary)

    _mark_posted(month)
    print(f"monthly_burn_snapshot: posted for {month}, wrote {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
