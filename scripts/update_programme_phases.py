#!/usr/bin/env python3
"""update_programme_phases.py — the OWNING LANE's update path for a build-status
programme's phases (Musa op#34918, orch-console 78503 plan / 78505 GO: "every
update carries the updating agent and an op# citation").

Each owning lane (cc-paperclip-spike-1 for substrate 2.0 + memory 2.0,
cc-bariq-1 for TG 2.0) maintains ONLY its own programme's phases jsonb:

  phases = {
    "owner":  <fixed at seed; an update can NEVER change ownership>,
    "phases": [{"name": ..., "status": done|building|next|blocked, "date": optional ISO date}],
    "last_progress": {"at": ISO ts, "line": one plain line},
    "next_checkpoint": ISO date,
    "updated_by": <agent>, "op": <op#N>, "updated_at": ISO ts
  }

ENFORCED IN CODE, not by promise (the bus_send lesson): --by MUST equal the
row's current phases.owner or the update refuses — a lane cannot write another
programme's phases, and nobody can silently re-own a programme. The audit
trail (updated_by / op / updated_at) rides inside the jsonb the console
renders, so the view itself shows who last touched a programme.

programme_stall_guard keeps its separate clock job — this helper maintains
display state, not the checkpoint invariant.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import psycopg

VALID_STATUSES = ("done", "building", "next", "blocked")


def _parse_phases_payload(raw: str) -> dict:
    payload = json.loads(raw)  # ValueError propagates as a crash trace: malformed input is a caller bug
    if not isinstance(payload, dict):
        raise SystemExit("✗ phases payload must be a JSON object")
    phases = payload.get("phases")
    if not isinstance(phases, list) or not phases:
        raise SystemExit("✗ phases payload needs a non-empty 'phases' list")
    for item in phases:
        if not isinstance(item, dict) or not item.get("name"):
            raise SystemExit("✗ every phase needs a name")
        if item.get("status") not in VALID_STATUSES:
            raise SystemExit(
                f"✗ phase status must be one of {VALID_STATUSES}, got: {item.get('status')!r}"
            )
    lp = payload.get("last_progress")
    if lp is not None and not (isinstance(lp, dict) and lp.get("at") and lp.get("line")):
        raise SystemExit("✗ last_progress must be {'at': ISO ts, 'line': text} (both required)")
    return payload


def update(dsn: str, programme_id: int, by: str, op: str, payload: dict) -> int:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, phases FROM operator_backlog WHERE id = %s", (programme_id,)
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise SystemExit(f"✗ no operator_backlog row with id={programme_id}")
        _, current = row
        current = current or {}

        owner = current.get("owner")
        if owner is None:
            conn.rollback()
            raise SystemExit(
                f"✗ programme {programme_id} has no phases.owner — it was not "
                f"seeded by seed_build_programmes.py; refusing to guess."
            )
        if by != owner:
            conn.rollback()
            raise SystemExit(
                f"✗ owner mismatch: programme {programme_id} belongs to {owner}; "
                f"updates come from the owning lane (--by {by} refused). "
                f"Enforced per orch-console 78505."
            )

        merged = dict(current)  # owner (and anything else) is preserved, never taken from payload
        merged["phases"] = payload["phases"]
        if "last_progress" in payload:
            merged["last_progress"] = payload["last_progress"]
        if payload.get("next_checkpoint"):
            merged["next_checkpoint"] = payload["next_checkpoint"]
        merged["updated_by"] = by
        merged["op"] = op
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()

        cur.execute(
            "UPDATE operator_backlog SET phases = %s, updated_at = now() WHERE id = %s",
            (json.dumps(merged), programme_id),
        )
        conn.commit()
        print(
            f"✓ programme {programme_id} phases updated by {by} ({op}) — "
            f"{len(payload['phases'])} phase(s)"
        )
        return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL")
    p.add_argument("--programme", type=int, required=True,
                   help="operator_backlog id of the programme row")
    p.add_argument("--by", required=True,
                   help="updating agent id — MUST equal the programme's phases.owner")
    p.add_argument("--op", required=True,
                   help="op# citation for this update (e.g. op#34918)")
    p.add_argument("--phases-json", required=True,
                   help="JSON object: {phases: [{name,status,date?}], last_progress?, next_checkpoint?} "
                        "(owner is never writable here)")
    args = p.parse_args(argv)
    dsn = args.dsn or __import__("os").environ.get("DATABASE_URL")
    if not dsn:
        print("✗ no DSN: pass --dsn or set DATABASE_URL", file=sys.stderr)
        return 2
    payload = _parse_phases_payload(args.phases_json)
    return update(dsn, args.programme, args.by, args.op, payload)


if __name__ == "__main__":
    sys.exit(main())
