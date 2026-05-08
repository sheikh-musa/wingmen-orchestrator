#!/usr/bin/env python3
"""measure_supabase_branch_coldstart — time how long a Supabase branch takes
to respond to its first query after a cold idle.

Per CAI-RESP-153 P1: required pre-ship check. >30s cold-start = follow-up
(may need to configure the branch to never idle, or build a CI warm-up
hook).

Usage:
  scripts/measure_supabase_branch_coldstart.py <postgresql-connection-string>

Procedure:
  1. Wait 30s (assume branch was idle before invocation; this script does not
     attempt to actively idle it — caller responsibility).
  2. Open a fresh connection.
  3. Time SELECT 1 round-trip.
  4. Time SELECT count(*) FROM pg_class round-trip (slightly heavier).
  5. Report both timings and a verdict (PASS if max < 30s, FAIL otherwise).

Exit codes:
  0 — both queries returned in <30s (cold-start within budget)
  1 — at least one query >30s (follow-up needed)
  2 — connection refused / DNS failure / other unrecoverable error
"""
from __future__ import annotations

import sys
import time
from typing import Tuple

try:
    import psycopg
except ImportError:
    print("ERROR: psycopg not installed", file=sys.stderr)
    sys.exit(2)


_BUDGET_SECONDS = 30.0


def _time_query(dsn: str, sql: str) -> Tuple[float, str | None]:
    """Open fresh connection + run sql. Returns (elapsed_seconds, error_str_or_None)."""
    start = time.monotonic()
    try:
        with psycopg.connect(dsn, connect_timeout=60) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.fetchall()
        return time.monotonic() - start, None
    except Exception as exc:
        return time.monotonic() - start, f"{type(exc).__name__}: {exc}"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"usage: {argv[0]} <postgresql-connection-string>", file=sys.stderr)
        return 2
    dsn = argv[1]

    print(f"Cold-start measurement starting (budget: {_BUDGET_SECONDS}s per query)")
    print(f"Note: caller should ensure branch was idle for >5min before this script.")
    print()

    queries = [
        ("SELECT 1", "trivial round-trip"),
        ("SELECT count(*) FROM pg_class", "lightweight catalog scan"),
    ]

    failed = False
    for sql, label in queries:
        elapsed, err = _time_query(dsn, sql)
        if err:
            print(f"  [{label}] FAIL after {elapsed:.2f}s: {err}")
            return 2
        verdict = "PASS" if elapsed < _BUDGET_SECONDS else "FAIL (over budget)"
        print(f"  [{label}] {elapsed:.2f}s — {verdict}")
        if elapsed >= _BUDGET_SECONDS:
            failed = True

    print()
    if failed:
        print("RESULT: cold-start over 30s budget. File follow-up per CAI-RESP-153 P1.")
        return 1
    print("RESULT: cold-start within budget. CI live-DB ship is unblocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
