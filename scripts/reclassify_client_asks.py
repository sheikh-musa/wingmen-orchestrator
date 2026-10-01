#!/usr/bin/env python3
"""reclassify_client_asks.py — one-time re-run of operator_log.classify_client_ask
over already-captured client-channel rows (orch-console bus #47184, follow-up to
the migration 085 backfill).

The backfill's original heuristic only screened out unambiguous bare acks, so
most of the 359 backfilled rows landed with triage_state left at the default/
non-'ask' state regardless of content -- 199 of them showed as overdue, mostly
cosem-caai chatter (Musa<->Arqam banter, acks, answers), not real client
requests. A ledger where 1 real ask hides among 20 chatter rows recreates
exactly the Shuq 44h-untracked failure (op#23944) this ledger exists to
prevent, and paging on it floods every lane. This one-off applies the new
classify_client_ask() bias (default not_an_ask; 'ask' only for a genuine
request/question) to what's already in the table.

NEVER touches a row a human has already triaged: only rows with
triaged_by IN (NULL, 'heuristic') are candidates -- this corrects the
heuristic's own prior pass, never a person's judgment call.

Post-reclassify numbers are PROJECTED in-memory from the changes this run
computed, not re-queried from the DB -- a re-query right after commit could
pick up unrelated concurrent inbound (ingest.py keeps running live) and
misattribute it to this reclassification. Same projection logic serves both
--dry-run (nothing written yet) and the real run (what was just written).

Usage:
  reclassify_client_asks.py [--dry-run]
    --dry-run   print what would change (grouped by lane/old->new), write nothing
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from nervous_system.operator_log import classify_client_ask  # noqa: E402

ORCH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(ORCH, ".env"))


def _dsn() -> "str | None":
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def fetch_open_rows(conn) -> list:
    """Every open (closed_at IS NULL) client-channel row -- the full picture
    needed to project accurate post-reclassify per-lane totals, not just the
    subset this script is allowed to change."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ask, delegated_to, triage_state, triaged_by, chase_by "
            "FROM operator_asks "
            "WHERE ask_surface = 'client-channel' AND closed_at IS NULL "
            "ORDER BY id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def reclassifiable(rows: list) -> list:
    """Rows the heuristic (not a human) last touched -- the only rows this
    script is allowed to change."""
    return [r for r in rows if r["triaged_by"] in (None, "heuristic")]


def compute_changes(rows: list) -> list:
    """Pure: (id, delegated_to, old_state, new_state) for every row whose
    reclassification differs from its current triage_state."""
    changes = []
    for r in rows:
        new_label = classify_client_ask(r["ask"])
        if new_label != r["triage_state"]:
            changes.append((r["id"], r["delegated_to"], r["triage_state"], new_label))
    return changes


def apply_changes(conn, changes: list) -> None:
    with conn.cursor() as cur:
        for rid, _lane, _old, new in changes:
            cur.execute(
                "UPDATE operator_asks SET triage_state=%s, triaged_at=now(), "
                "triaged_by='heuristic' WHERE id=%s",
                (new, rid),
            )
    conn.commit()


def project_post_state(rows: list, changes: list) -> list:
    """Pure: overlay `changes` onto `rows` in-memory -- what the table will
    look like right after apply_changes(), without a second DB round-trip."""
    new_state_by_id = {rid: new for rid, _lane, _old, new in changes}
    projected = []
    for r in rows:
        state = new_state_by_id.get(r["id"], r["triage_state"])
        projected.append({**r, "triage_state": state})
    return projected


def summarize_by_lane(rows: list, now=None) -> list:
    """Pure: per-lane ask/overdue_ask/total counts (no-PII: counts only) --
    what gets reported back to orch-console per bus #47184 item 4."""
    now = now or datetime.now(timezone.utc)
    by_lane: dict = {}
    for r in rows:
        lane = r["delegated_to"] or "?"
        agg = by_lane.setdefault(lane, {"delegated_to": lane, "asks": 0, "overdue_asks": 0, "total": 0})
        agg["total"] += 1
        if r["triage_state"] == "ask":
            agg["asks"] += 1
            if r["chase_by"] is not None and r["chase_by"] < now:
                agg["overdue_asks"] += 1
    return sorted(by_lane.values(), key=lambda a: a["total"], reverse=True)


def render_summary(changes: list, lane_summary: list) -> str:
    """Pure formatting, no DB/network."""
    by_move: dict = {}
    for _id, lane, old, new in changes:
        key = (lane or "?", old, new)
        by_move[key] = by_move.get(key, 0) + 1
    lines = [f"reclassify_client_asks: {len(changes)} row(s) changed"]
    for (lane, old, new), n in sorted(by_move.items()):
        lines.append(f"  {lane}: {old} -> {new}: {n}")
    lines.append("")
    lines.append("Post-reclassify open client-channel set by lane:")
    for r in lane_summary:
        lines.append(f"  {r['delegated_to']}: {r['asks']} ask ({r['overdue_asks']} overdue) / {r['total']} total open")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would change, write nothing")
    args = ap.parse_args(argv)

    dsn = _dsn()
    if not dsn:
        print("reclassify_client_asks: no DATABASE_URL/SUPABASE_DB_URL", file=sys.stderr)
        return 2

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        rows = fetch_open_rows(conn)
        candidates = reclassifiable(rows)
        changes = compute_changes(candidates)
        print(f"reclassify_client_asks: {len(candidates)} candidate row(s), {len(changes)} would change")
        if not args.dry_run:
            apply_changes(conn, changes)
        projected = project_post_state(rows, changes)
        print(render_summary(changes, summarize_by_lane(projected)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
