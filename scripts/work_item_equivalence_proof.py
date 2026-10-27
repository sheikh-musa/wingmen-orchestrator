#!/usr/bin/env python3
"""work_item_equivalence_proof.py -- row-for-row proof that the work_item SSOT equals
its three sources (bus #73176 item 5; #73179 "row-for-row, not count-for-count";
Musa op#34969). Read-only. Must be CLEAN before ANY reader is cut over.

THE PROPOSITION, per kind:
  (P1) every OPEN source row has EXACTLY ONE LIVE work_item row, and
  (P2) every LIVE work_item row has exactly one OPEN source row, and
  (P3) for every such pair, work_item.(state, owner_agent) equals what the migration's
       own work_item_derive_<kind>() derives from the source row right now
       (one definition -- the trigger, the backfill and this proof all call it).

"OPEN" is each source's own canonical notion, taken from the sweep that owns that
domain today (the readers to be cut over, #73176 item 6):
  ask         operator_asks.closed_at IS NULL AND triage_state NOT IN ('done','not_an_ask')
              -- asks_reconcile_daily's CLOSE step treats a done/not_an_ask row with a
              NULL closed_at as DRIFT to be closed, not as open; work_item holds those
              as done/dropped already. Reported separately as "legacy CLOSE-drift".
  bus         agent_messages.requires_response AND responded_at IS NULL AND skipped_at IS NULL
              -- reconcile_responded_at's raw filter (RR AND responded_at IS NULL)
              includes rows priority_sla_watchdog has SKIPPED; those are terminal
              ('dropped') in work_item. Reported separately as "legacy skipped".
  commitment  held_commitments.status IN ('pending','fired')  (commitment_sweeper).

"LIVE" = work_item.state IN ('captured','owned','in_progress').

Uniqueness in (P1) is structural (work_item_source_uk UNIQUE (source_table, source_id)),
and re-asserted here by comparing id SETS and COUNTS both ways, not just counts.

Output: a markdown proof artifact (--out), exit 0 when every proposition holds, 1 when
any fails. The artifact is what goes to the orch-console gate before a cutover.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent))
import work_item_reconcile as wir  # noqa: E402

LEGACY_DELTAS = {
    # kind -> (label, predicate over alias s selecting rows the LEGACY sweep's raw filter
    #          would call open but the source itself treats as terminal, expected wi state)
    "ask": ("legacy CLOSE-drift (closed_at NULL but triage terminal)",
            "s.closed_at IS NULL AND s.triage_state IN ('done','not_an_ask')",
            ("done", "dropped")),
    "bus": ("legacy skipped (RR, unresponded, skipped_at set)",
            "s.requires_response AND s.responded_at IS NULL AND s.skipped_at IS NOT NULL",
            ("dropped",)),
}


def _ids(cur, sql, params=()) -> Set[int]:
    cur.execute(sql, params)
    return {r[0] for r in cur.fetchall()}


def prove(conn, *, now: Optional[datetime] = None) -> Dict:
    """Returns {'ok': bool, 'kinds': {kind: {...}}, 'divergence': [...]}."""
    now = now or datetime.now(timezone.utc)
    result: Dict = {"at": now.isoformat(), "ok": True, "kinds": {}, "divergence": []}
    with conn.cursor() as cur:
        for kind, k in wir.KINDS.items():
            open_ids = _ids(cur, f"SELECT s.id FROM {k['table']} s WHERE {k['open_pred']}")
            live_ids = _ids(cur, "SELECT source_id FROM work_item WHERE source_table = %s "
                                 "AND state IN ('captured','owned','in_progress')", (k["table"],))
            cur.execute("SELECT count(*), count(DISTINCT source_id) FROM work_item "
                        "WHERE source_table = %s", (k["table"],))
            wi_rows, wi_distinct = cur.fetchone()
            entry = {
                "open_source": len(open_ids), "live_work_item": len(live_ids),
                "open_without_live": sorted(open_ids - live_ids),
                "live_without_open": sorted(live_ids - open_ids),
                "work_item_rows": wi_rows, "work_item_distinct_sources": wi_distinct,
                "legacy_delta": None,
            }
            if kind in LEGACY_DELTAS:
                label, pred, expected = LEGACY_DELTAS[kind]
                delta_ids = _ids(cur, f"SELECT s.id FROM {k['table']} s WHERE {pred}")
                cur.execute("SELECT source_id FROM work_item WHERE source_table = %s "
                            "AND source_id = ANY(%s) AND state = ANY(%s)",
                            (k["table"], sorted(delta_ids), list(expected)))
                explained = {r[0] for r in cur.fetchall()}
                entry["legacy_delta"] = {
                    "label": label, "count": len(delta_ids),
                    "unexplained": sorted(delta_ids - explained),  # no terminal wi row
                    "expected_state": list(expected),
                }
                if entry["legacy_delta"]["unexplained"]:
                    # A legacy-terminal row that was open at backfill time MUST have been
                    # mirrored to its terminal state; one that was already closed before
                    # 114 landed legitimately has no row. Only flag rows that DO have a
                    # work_item row in a non-expected state.
                    cur.execute("SELECT source_id FROM work_item WHERE source_table = %s "
                                "AND source_id = ANY(%s)",
                                (k["table"], entry["legacy_delta"]["unexplained"]))
                    wrong = sorted(r[0] for r in cur.fetchall())
                    entry["legacy_delta"]["wrong_state"] = wrong
                    if wrong:
                        result["ok"] = False
            if entry["open_without_live"] or entry["live_without_open"] or wi_rows != wi_distinct:
                result["ok"] = False
            result["kinds"][kind] = entry

    # (P3) state/owner equality + the structural classes, via the one shared detector.
    # ABANDONED is a liveness page, not an equivalence failure -- excluded here.
    div = wir.find_divergence(conn, now=now, include_abandoned=False)
    result["divergence"] = [f.line() for f in div]
    if div:
        result["ok"] = False
    return result


def render(result: Dict) -> str:
    lines = [f"# work_item equivalence proof -- {'PASS' if result['ok'] else 'FAIL'}",
             f"at: {result['at']}", "",
             "| kind | open source | live work_item | open w/o live | live w/o open | wi rows | distinct sources |",
             "|---|---|---|---|---|---|---|"]
    for kind, e in result["kinds"].items():
        lines.append(f"| {kind} | {e['open_source']} | {e['live_work_item']} | "
                     f"{len(e['open_without_live'])} | {len(e['live_without_open'])} | "
                     f"{e['work_item_rows']} | {e['work_item_distinct_sources']} |")
    lines.append("")
    for kind, e in result["kinds"].items():
        if e["open_without_live"]:
            lines.append(f"- {kind}: OPEN source rows with no LIVE work_item: {e['open_without_live'][:50]}")
        if e["live_without_open"]:
            lines.append(f"- {kind}: LIVE work_item rows with no OPEN source: {e['live_without_open'][:50]}")
        d = e.get("legacy_delta")
        if d:
            lines.append(f"- {kind}: {d['label']}: {d['count']} row(s); expected work_item state "
                         f"{d['expected_state']}; wrong-state rows: {d.get('wrong_state', [])}")
    lines.append("")
    lines.append(f"## (P3) state/owner equality + structural divergence: "
                 f"{len(result['divergence'])} finding(s)")
    lines += [f"    {ln}" for ln in result["divergence"][:200]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="row-for-row work_item equivalence proof (read-only)")
    ap.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL")
    ap.add_argument("--out", type=Path, default=None, help="write the markdown artifact here")
    args = ap.parse_args(argv)
    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        print("work_item_equivalence_proof: no --dsn and DATABASE_URL unset", file=sys.stderr)
        return 1
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        result = prove(conn)
    text = render(result)
    print(text, end="")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
