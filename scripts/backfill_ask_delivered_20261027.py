#!/usr/bin/env python3
"""One-off: backfill operator_asks.delivered_at for 2026-10-27's four LIVE-verified asks
(op#34803; orch-console #78198 gave the LIVE times from its relay messages).

Run ONLY after migration 121 is applied. Safe by construction:
  * each row is matched on BOTH ask id and the outbound_msg_id of console's LIVE relay, so a
    wrong id can't be stamped;
  * `AND delivered_at IS NULL`: first stamp wins, so a re-run is a no-op;
  * --dry-run (default) prints what would change; --apply writes, in one transaction.
"""
from __future__ import annotations

import os
import sys

import psycopg

# (ask_id, op#, outbound_msg_id of console's LIVE relay, LIVE time per console #78198)
ROWS = [
    (5367, 34758, 34781, "2026-10-27 13:28:00+00"),   # Link-as-Trainer (bundle verified 13:28Z)
    (5368, 34759, 34785, "2026-10-27 13:53:00+00"),   # Feature access folding
    (5343, 34701, 34788, "2026-10-27 14:21:00+00"),   # Facemask / Backplate / LDV rename
    (5378, 34789, 34802, "2026-10-27 14:52:00+00"),   # Name edit fix (deploy 14:52Z)
]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 2
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        changed = 0
        for ask_id, op, ob, ts in ROWS:
            cur.execute(
                "UPDATE operator_asks SET delivered_at = %s::timestamptz "
                "WHERE id = %s AND source_msg_id = %s AND outbound_msg_id = %s "
                "AND delivered_at IS NULL RETURNING id",
                (ts, ask_id, op, ob))
            hit = cur.fetchone() is not None
            changed += hit
            print(f"ask {ask_id} op#{op}: {'stamp ' + ts if hit else 'no change (already set or ids do not match)'}")
        if apply:
            conn.commit()
            print(f"APPLIED: {changed} row(s)")
        else:
            conn.rollback()
            print(f"DRY-RUN: {changed} row(s) would change (rolled back); pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
