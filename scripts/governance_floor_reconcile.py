#!/usr/bin/env python3
"""governance_floor_reconcile.py — evaluate every governance_floor.predicate_sql
row and record pass/fail (op#35246 Phase 1a, PR#555, reports/joint-plan-20261028/
PLAN.md section 2).

Same overall shape as work_item_reconcile.py (dry-run by default, read-only
unless an explicit flag commits writes), but deliberately has no --repair:
there is no code-level fix for a failing floor -- a human resolves the
underlying violation -- so this script only ever evaluates and, with
--record, writes the audit trail. Never touches governance_floor itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg

sys.path.insert(0, os.path.dirname(__file__))
import bus_send  # noqa: E402 — reuse its dburl() so DSN resolution has one owner


def evaluate(dsn: str) -> list[dict]:
    """Returns one result per governance_floor row: {id, op_ref, sentence,
    passing, error}. A predicate that fails to even evaluate (malformed SQL)
    counts as failing, with `error` set, rather than crashing the run -- one
    bad floor row must not hide every other floor's result."""
    results = []
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, op_ref, sentence, predicate_sql FROM governance_floor "
                    "WHERE retired_at IS NULL ORDER BY id")
        floors = cur.fetchall()
        for floor_id, op_ref, sentence, predicate_sql in floors:
            try:
                with conn.cursor() as pcur:
                    pcur.execute(f"SELECT ({predicate_sql})")
                    passing = bool(pcur.fetchone()[0])
                    error = None
            except psycopg.Error as exc:
                conn.rollback()
                passing = False
                error = str(exc).strip()
            results.append(
                {"id": floor_id, "op_ref": op_ref, "sentence": sentence, "passing": passing, "error": error}
            )
    return results


def record(dsn: str, results: list[dict]) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for r in results:
            event = "checked_pass" if r["passing"] else "checked_fail"
            cur.execute(
                "INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, note) "
                "VALUES (%s, %s, %s, %s, %s)",
                (r["id"], event, "governance_floor_reconcile.py", r["op_ref"], r["error"]),
            )
        conn.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL")
    ap.add_argument("--record", action="store_true", help="write checked_pass/checked_fail audit rows")
    ap.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    args = ap.parse_args(argv)

    dsn = args.dsn or bus_send.dburl(os.environ.get("DATABASE_URL"))
    results = evaluate(dsn)

    if args.record:
        record(dsn, results)

    total = len(results)
    passing = sum(1 for r in results if r["passing"])

    if args.json:
        print(json.dumps({"total": total, "passing": passing, "results": results}))
    else:
        for r in results:
            status = "PASS" if r["passing"] else "FAIL"
            suffix = f" ({r['error']})" if r["error"] else ""
            print(f"[{status}] {r['op_ref']} {r['sentence']}{suffix}")
        print(f"{passing} of {total} floors passing")

    return 0 if passing == total else 2


if __name__ == "__main__":
    sys.exit(main())
