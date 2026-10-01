#!/usr/bin/env python3
"""backfill_client_asks_ledger.py — one-time backfill of the last N days of
client-channel inbound into operator_asks (migration 085, Musa op#23944, bus
#47105 -> #47114 item 4): before migration 085 shipped, operator_asks had
ZERO rows for any client-group message (the root cause of Shuq's 44h-
untracked ask), so the ledger's "current open set" has to be built
retroactively from operator_messages history rather than starting empty on
the day the capture path goes live.

Backdates created_at/chase_by to the ORIGINAL message time via
nervous_system.operator_log.maybe_track_client_ask's `opened_at` param — a
message that's already 3 days old must show as already overdue, not get a
fresh 24h grace period it never actually had.

Idempotent: only considers operator_messages rows that don't already have a
matching operator_asks.source_msg_id row, so a second run (partial failure,
retry, or running again after the going-forward ingest.py capture path is
already live) never double-inserts.

No-PII rule (same as scripts/asks_daily_digest.py): the post-backfill report
never prints raw message text, only triage_summary (empty for a freshly
backfilled row until someone triages it), delegated_to, triage_state, and
age.

Usage:
  backfill_client_asks_ledger.py [--dry-run] [--days N]
    --dry-run   print what would be inserted (grouped by channel), write nothing
    --days N    lookback window (default 7, per bus #47105 item 4)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from nervous_system import operator_log  # noqa: E402

ORCH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(ORCH, ".env"))


def _dsn() -> "str | None":
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def fetch_candidates(conn, since) -> list:
    """Inbound operator_messages on a bot_channels.audience='client' channel,
    within the lookback window, that don't already have a matching
    operator_asks row (source_msg_id) -- the idempotent re-run guard. Joins
    on channel_tag (not channel_key): operator_messages.tag is stamped from
    Channel.channel_tag at log() time (nervous_system/ingest.py), same
    convention maybe_track_ask() already relies on for the operator path."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT om.id, om.text, om.created_at, bc.owner_lane, bc.channel_key "
            "FROM operator_messages om "
            "JOIN bot_channels bc ON bc.channel_tag = om.tag "
            "WHERE om.direction = 'inbound' AND bc.audience = 'client' "
            "  AND om.created_at >= %s "
            "  AND NOT EXISTS (SELECT 1 FROM operator_asks oa WHERE oa.source_msg_id = om.id) "
            "ORDER BY om.created_at ASC",
            (since,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_open_client_set(conn) -> list:
    """Post-backfill report source: every OPEN client-channel row. No raw
    `ask` text selected at all -- only triage_summary (same no-PII rule as
    asks_daily_digest.fetch_client_open_asks)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, delegated_to, triage_state, triage_summary, created_at, chase_by "
            "FROM operator_asks WHERE closed_at IS NULL AND ask_surface = 'client-channel' "
            "ORDER BY created_at ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def render_report(rows: list, now=None) -> str:
    """Pure formatting, no DB/network -- trivially unit-testable."""
    now = now or datetime.now(timezone.utc)
    if not rows:
        return "backfill_client_asks_ledger: open client-channel set is empty."
    lines = [f"Client-channel open set -- {len(rows)} row(s):"]
    for r in rows:
        age_h = (now - r["created_at"]).total_seconds() / 3600
        age = f"{age_h:.0f}h" if age_h < 48 else f"{age_h / 24:.1f}d"
        overdue = r["chase_by"] is not None and r["chase_by"] < now
        flag = " OVERDUE" if overdue else ""
        summary = f": {r['triage_summary']}" if r.get("triage_summary") else " (untriaged)"
        lines.append(f"  #{r['id']} ({r.get('delegated_to') or '?'}, {r['triage_state']}, "
                     f"age {age}{flag}){summary}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would be inserted, write nothing")
    ap.add_argument("--days", type=int, default=7, help="lookback window in days (default 7)")
    args = ap.parse_args(argv)

    dsn = _dsn()
    if not dsn:
        print("backfill_client_asks_ledger: no DATABASE_URL/SUPABASE_DB_URL", file=sys.stderr)
        return 2

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        candidates = fetch_candidates(conn, since)

    if args.dry_run:
        print(f"backfill_client_asks_ledger: {len(candidates)} candidate row(s) "
              f"in the last {args.days}d (dry-run, writing nothing)")
        by_lane: dict = {}
        for c in candidates:
            key = f"{c['channel_key']} -> {c.get('owner_lane') or '?'}"
            by_lane[key] = by_lane.get(key, 0) + 1
        for key, n in sorted(by_lane.items()):
            print(f"  {key}: {n}")
        return 0

    for c in candidates:
        operator_log.maybe_track_client_ask(
            c["id"], c["text"], c.get("owner_lane"), opened_at=c["created_at"],
        )
    print(f"backfill_client_asks_ledger: inserted {len(candidates)} row(s) "
          f"from the last {args.days}d")

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        open_rows = fetch_open_client_set(conn)
    print()
    print(render_report(open_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
