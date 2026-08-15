#!/usr/bin/env python3
"""asks_close.py — apply an operator swipe-to-confirm on one operator_asks row.

The fleet console's DB session is read-only by construction, so the "Your asks"
swipe POST handler (nervous_system/console/app.py, /api/ask-close) shells out to
THIS script to perform the write — the same vetted-script pattern /api/reset and
scripts/backlog_swipe.py use. Runs in the orchestrator env (writable DATABASE_URL).

Usage:  asks_close.py <id> <confirm|drop>

Actions (op#13250 — the operator's swipe on a "Your asks" card):
  confirm   the operator confirms a delegate-reported-done ask ACTUALLY landed —
            sets confirmed_at=now(), closed_at=now(), closed_reason='operator_done'.
            (A lane stamping responded_at only shows REVIEW; the operator's confirm
            is the ONLY authoritative done — delegate-replied ≠ operator-confirmed.)
  drop      the operator dismisses the ask — sets closed_at=now(),
            closed_reason='dropped' (row kept in-table for history).

Both close the ask (closed_at set) so it leaves the live board (which filters
closed_at IS NULL). Status itself is NEVER stored — it's derived live from the
bus — so there is nothing else to write.

Exit 0 + prints 'ok' on exactly one row updated; exit 2 + 'error: ...' otherwise
(unknown id / bad action / no DSN), so the console can surface a clean failure.
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: asks_close.py <id> <confirm|drop>\n")
        return 2
    raw_id, action = sys.argv[1], sys.argv[2].strip().lower()
    try:
        item_id = int(raw_id)
    except ValueError:
        sys.stderr.write(f"error: id must be an integer, got {raw_id!r}\n")
        return 2
    if action not in ("confirm", "drop"):
        sys.stderr.write(f"error: unknown action {action!r}\n")
        return 2

    load_dotenv()
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.stderr.write("error: no DATABASE_URL\n")
        return 2

    if action == "confirm":
        # Operator-confirmed done: stamp BOTH confirmed_at and closed_at. Idempotent
        # on closed_at IS NULL so a double-tap can't reopen/rewrite a closed row.
        sql = (
            "UPDATE operator_asks SET "
            "  confirmed_at = now(), closed_at = now(), closed_reason = 'operator_done' "
            "WHERE id = %s AND closed_at IS NULL"
        )
    else:  # drop
        sql = (
            "UPDATE operator_asks SET "
            "  closed_at = now(), closed_reason = 'dropped' "
            "WHERE id = %s AND closed_at IS NULL"
        )

    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [item_id])
                n = cur.rowcount
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"error: {type(e).__name__}: {e}\n")
        return 2

    if n == 1:
        print("ok")
        return 0
    sys.stderr.write(f"error: {n} rows updated (id {item_id} not found or already closed?)\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
