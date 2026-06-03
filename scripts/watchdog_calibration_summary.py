"""CC-WATCHDOG-CALIBRATION-001 day-30 summary per CAI-RESP-168 §5.

Reads watchdog_calibration_observations + notification_log + watchdog_monitored_callers
for the configured window and reports:

  - Total observations + breakdown by action (hard_kill / monitored / no_kill / no_action)
  - Per-CC distribution
  - signal_a near-miss count (15% band around 80KB → 68KB ≤ value ≤ 92KB)
  - signal_a median + p10/p90 distribution overall and per action
  - Threshold-lock-in recommendation hooks (operator interprets)

Usage:
    python scripts/watchdog_calibration_summary.py                # default: 30d window
    python scripts/watchdog_calibration_summary.py --days 7       # rolling 7d
    python scripts/watchdog_calibration_summary.py --since 2026-05-26
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
SIGNAL_A_THRESHOLD = 80 * 1024  # 80KB per CAI-RESP-164
NEAR_MISS_LO = 68 * 1024        # 80KB - 15%
NEAR_MISS_HI = 92 * 1024        # 80KB + 15%


def _summarise(cur, since: str) -> None:
    cur.execute(
        """
        SELECT count(*),
               sum(CASE WHEN action='hard_kill' THEN 1 ELSE 0 END)  AS n_hard_kill,
               sum(CASE WHEN action='monitored' THEN 1 ELSE 0 END)  AS n_monitored,
               sum(CASE WHEN action='no_kill'   THEN 1 ELSE 0 END)  AS n_no_kill,
               sum(CASE WHEN action='no_action' THEN 1 ELSE 0 END)  AS n_no_action,
               sum(CASE WHEN signal_a_near_miss THEN 1 ELSE 0 END)  AS n_near_miss
          FROM watchdog_calibration_observations
         WHERE observed_at >= %s
        """,
        (since,),
    )
    row = cur.fetchone()
    if not row or row[0] == 0:
        print(f"NO OBSERVATIONS since {since}")
        return
    total, n_hk, n_mon, n_nk, n_na, n_near = row
    print(f"Window since: {since}")
    print(f"Total observations: {total}")
    print(f"  hard_kill : {n_hk:>5}  ({100*n_hk/total:.1f}%)")
    print(f"  monitored : {n_mon:>5}  ({100*n_mon/total:.1f}%)")
    print(f"  no_kill   : {n_nk:>5}  ({100*n_nk/total:.1f}%)")
    print(f"  no_action : {n_na:>5}  ({100*n_na/total:.1f}%)")
    print()
    print(f"signal_a near-misses (68–92KB band, ±15% of 80KB): {n_near}  ({100*n_near/total:.1f}%)")
    print()

    cur.execute(
        """
        SELECT cc_identity, action, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY signal_a_value)::INTEGER AS p50,
               percentile_cont(0.1) WITHIN GROUP (ORDER BY signal_a_value)::INTEGER AS p10,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY signal_a_value)::INTEGER AS p90
          FROM watchdog_calibration_observations
         WHERE observed_at >= %s
           AND signal_a_value IS NOT NULL
         GROUP BY cc_identity, action
         ORDER BY cc_identity, action
        """,
        (since,),
    )
    rows = cur.fetchall()
    if rows:
        print("Per-CC × action × signal_a distribution:")
        print(f"  {'cc_identity':30s} {'action':10s} {'n':>5} {'p10/KB':>7} {'p50/KB':>7} {'p90/KB':>7}")
        for cc, act, n, p50, p10, p90 in rows:
            p50_kb = (p50 or 0) / 1024
            p10_kb = (p10 or 0) / 1024
            p90_kb = (p90 or 0) / 1024
            print(f"  {cc:30s} {act:10s} {n:>5} {p10_kb:>7.1f} {p50_kb:>7.1f} {p90_kb:>7.1f}")
        print()

    cur.execute(
        """
        SELECT cc_identity, count(*) AS n,
               min(signal_a_value) AS min_v,
               max(signal_a_value) AS max_v
          FROM watchdog_calibration_observations
         WHERE observed_at >= %s
           AND signal_a_near_miss = true
         GROUP BY cc_identity
         ORDER BY n DESC
        """,
        (since,),
    )
    near_rows = cur.fetchall()
    if near_rows:
        print(f"Near-miss callers (signal_a in 68–92KB band):")
        for cc, n, lo, hi in near_rows:
            print(f"  {cc:30s} {n:>5} obs  range {lo/1024:.1f}–{hi/1024:.1f} KB")
        print()

    cur.execute(
        """
        SELECT count(*) FROM notification_log
         WHERE source = 'watchdog_hard_kill' AND created_at >= %s
        """,
        (since,),
    )
    n_audit_hk = cur.fetchone()[0]
    cur.execute(
        """
        SELECT count(*) FROM notification_log
         WHERE source = 'watchdog_aborted_kill' AND created_at >= %s
        """,
        (since,),
    )
    n_audit_abort = cur.fetchone()[0]
    print(f"notification_log corroboration (existing audit):")
    print(f"  watchdog_hard_kill   : {n_audit_hk}")
    print(f"  watchdog_aborted_kill: {n_audit_abort}")
    print()

    print("RECOMMENDATION HOOKS (operator interprets):")
    if n_hk == 0:
        print("  - Zero hard_kills in window. Threshold safe to keep but no FP/TP data either.")
    if n_near > 0 and n_hk == 0:
        print(f"  - {n_near} signal_a near-misses with zero hard_kills → real callers approach the threshold without false-positive. Threshold is well-calibrated; consider keeping.")
    if n_near > 0 and n_hk > 0:
        print(f"  - {n_near} near-misses AND {n_hk} hard_kills → review the hard_kill audit rows for FP review.")
    if total > 0 and n_na / total > 0.5:
        print(f"  - {100*n_na/total:.0f}% no_action (signal unobservable). Investigate jsonl read paths or filesystem health.")


def main() -> int:
    if not DSN:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--since", type=str, default=None,
                   help="Explicit ISO datetime (else now - days)")
    args = p.parse_args()

    if args.since:
        since = args.since
    else:
        from datetime import timedelta
        since_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
        since = since_dt.isoformat()

    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        _summarise(cur, since)
    return 0


if __name__ == "__main__":
    sys.exit(main())
