#!/usr/bin/env python3
"""asks_daily_digest.py — a once-daily operator digest of open operator_asks
(op#22669, piece 4 second half: "if I ask 1000 things I expect you to track
1001" — a daily roll-up so nothing quietly ages off the operator's radar
between the priority_sla_watchdog's chase net firings).

Deliberately SIMPLE — this is NOT priority_sla_watchdog's dedup/backoff/
lease-gating machinery. It sends AT MOST ONE digest per UTC calendar day (a
persisted date-stamp in $ORCH/logs/asks_daily_digest_state.json is the only
dedup), so a launchd misfire or manual re-run can never double-send by
construction. Sends via scripts/nazim_send.sh (orch-console bus #43972, PR
#180 change 2) — this launchd job is Mini/console-hosted and the asks ledger
(op#22669) is Nazim's own thread, not the hub's. scripts/tg_send.sh fail-closes
for the console body under ORCH-TOPOLOGY-001 (orch_lease.py), so sending via it
from here would silently fail every single day and never reach the operator.

Scheduled via launchd/dev.wingmen.asks-daily-digest.plist at 09:00 Abu Dhabi
time (UTC+4) — see that file's comment for the host-TZ-hour conversion.
Landing this file does NOT wire it into launchd; a plist must still be
separately `launchctl load`ed at go-live, so this is safe to merge without
firing.

Robustness (bus #44286: the plist fired 8h off because launchd's
StartCalendarInterval is host-local and the host is not UTC): every real
send logs the wall-clock time in Asia/Dubai it actually fired at, and warns
loudly if that's far from the intended 09:00 slot — so a future host-TZ
change or launchd misconfiguration shows up in the log the same day instead
of waiting for the operator to notice a digest at the wrong hour.

Usage:
  asks_daily_digest.py [--dry-run] [--force]
    --dry-run   print the digest, send nothing, stamp nothing (safe to run any time)
    --force     send even if already sent today (manual re-run / testing)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
STATE_FILE = ORCH / "logs" / "asks_daily_digest_state.json"
UAE_TZ = ZoneInfo("Asia/Dubai")

load_dotenv(ORCH / ".env")


def _dsn() -> "str | None":
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def fetch_open_asks(conn) -> list:
    """Every OPEN operator_asks row, waiting-on-you first (op#22669's top
    priority — mirrors db.py's build_asks_query() 'waiting_on_musa' ordering),
    then oldest-first within each group."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ask, delegated_to, waiting_on_operator, chase_by, created_at "
            "FROM operator_asks WHERE closed_at IS NULL "
            "ORDER BY waiting_on_operator DESC, created_at ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def render_digest(rows: list) -> str:
    """Pure formatting — no DB/network — so this is trivially unit-testable."""
    if not rows:
        return "📋 Daily asks digest — nothing open. The ledger is clear."
    waiting = [r for r in rows if r.get("waiting_on_operator")]
    others = [r for r in rows if not r.get("waiting_on_operator")]
    lines = [f"📋 Daily asks digest — {len(rows)} open ({len(waiting)} waiting on you)"]
    if waiting:
        lines.append("")
        lines.append("WAITING ON YOU:")
        for r in waiting:
            lines.append(f"  #{r['id']} ({r.get('delegated_to') or '?'}): {r['ask']}")
    if others:
        lines.append("")
        lines.append("Open / in progress:")
        for r in others:
            lines.append(f"  #{r['id']} ({r.get('delegated_to') or 'unassigned'}): {r['ask']}")
    return "\n".join(lines)


def _already_sent_today(today: str) -> bool:
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        return False
    return state.get("last_sent_date") == today


def _mark_sent(today: str) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"last_sent_date": today}))
    except Exception:
        pass


def _log_uae_send_time(now_utc: "datetime | None" = None) -> None:
    """bus #44286: log the actual send time in Asia/Dubai, and warn loudly if
    it's far from the intended 09:00 UAE slot — a host-TZ or launchd-Hour
    drift then shows up in the log the day it happens, not when the operator
    notices a digest at the wrong hour."""
    now_uae = (now_utc or datetime.now(timezone.utc)).astimezone(UAE_TZ)
    line = f"asks_daily_digest: send time {now_uae.strftime('%Y-%m-%d %H:%M %Z')} (target ~09:00 Asia/Dubai)"
    if abs(now_uae.hour - 9) >= 2:
        line += (" -- WARNING: more than 1h off the intended 09:00 UAE slot; "
                 "check launchd Hour vs host TZ (bus #44286)")
    print(line)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                     help="print the digest, send nothing, stamp nothing")
    ap.add_argument("--force", action="store_true",
                     help="send even if already sent today (manual re-run)")
    args = ap.parse_args(argv)

    today = datetime.now(timezone.utc).date().isoformat()
    if not args.force and not args.dry_run and _already_sent_today(today):
        print(f"asks_daily_digest: already sent for {today} — skipping (use --force to resend)")
        return 0

    dsn = _dsn()
    if not dsn:
        print("asks_daily_digest: no DATABASE_URL/SUPABASE_DB_URL", file=sys.stderr)
        return 2

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        rows = fetch_open_asks(conn)
    digest = render_digest(rows)

    if args.dry_run:
        print(digest)
        return 0

    _log_uae_send_time()
    result = subprocess.run(["bash", str(ORCH / "scripts" / "nazim_send.sh"), digest])
    if result.returncode != 0:
        print("asks_daily_digest: nazim_send.sh failed — not stamping (retry next run)", file=sys.stderr)
        return 1
    _mark_sent(today)
    print(f"asks_daily_digest: sent ({len(rows)} open asks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
