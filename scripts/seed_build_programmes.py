#!/usr/bin/env python3
"""seed_build_programmes.py — create the THREE build-status programme rows
(Musa op#34918, orch-console 78503 plan / 78505 GO ruling).

78505: THREE operator_backlog rows, because Musa named three things and wants
to see each move:
  1. substrate 2.0                     — owner cc-paperclip-spike-1
  2. memory 2.0 incl second brain      — owner cc-paperclip-spike-1
     (rows 1+2: the joint plan, bus #78481, supplies their phases)
  3. TG 2.0 (per-client channels,      — owner cc-bariq-1
     invite deep-link onboarding, Bariq as client 1)

Each row gets a phases jsonb (migration 122 column) seeded with the real
state known at seed time — owning lanes refine it from there via
scripts/update_programme_phases.py. Each programme also gets ONE live
held_commitments checkpoint (payload {"backlog_id": N}, status pending) so
programme_stall_guard's invariant holds from the first minute: an open
programme with no live checkpoint pages once a day, and day-one seed noise
would be a false alarm.

FAIL-CLOSED: refuses if operator_backlog has no `phases` column (migration
122 not applied) rather than half-seeding rows the view can never see.

Idempotent: three rows already present under op_ref='op#34918' -> no-op.
Never writes explicit ids (identity column rule); DSN defaults to
$DATABASE_URL. Run only AFTER the migration is applied on the target store.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
OP_REF = "op#34918"
CREATED_BY = "cc-console-revamp-1"
CHECKPOINT_DAYS = 7

# Real state as of 2026-10-28, from the fleet record the joint plan and TG 2.0
# briefs cite — owning lanes own these phases from the first helper update on.
PROGRAMMES = [
    {
        "ask": "Substrate 2.0 — one joint plan with memory 2.0 (durable inbox, auto-recycle)",
        "phases": {
            "owner": "cc-paperclip-spike-1",
            "phases": [
                {"name": "Durable inbox step 2 (PR#490)", "status": "building"},
                {"name": "Substrate 2.0 fully operational", "status": "next"},
            ],
            "last_progress": {
                "at": "2026-10-27T16:16:07Z",
                "line": "PR#490 renumbered to 122/123/124, CI green (bus 78345)",
            },
            "seeded_by": CREATED_BY,
            "seed_op": OP_REF,
        },
    },
    {
        "ask": "Memory 2.0 + second brain — vault consolidation, conversation capture, anti-junk rules",
        "phases": {
            "owner": "cc-paperclip-spike-1",
            "phases": [
                {"name": "Vault consolidation design", "status": "building"},
                {"name": "Second brain — fleet populates the vault (op#34906)", "status": "next"},
                {"name": "Anti-junk rules + two-layer links (op#34910)", "status": "next"},
            ],
            "last_progress": {
                "at": "2026-10-27T17:45:31Z",
                "line": "Musa: ONE system for the whole substrate (op#34914)",
            },
            "seeded_by": CREATED_BY,
            "seed_op": OP_REF,
        },
    },
    {
        "ask": "TG 2.0 — per-client channels + invite deep-link onboarding, Bariq as client 1",
        "phases": {
            "owner": "cc-bariq-1",
            "phases": [
                {"name": "Per-client channel design", "status": "next"},
                {"name": "Bariq as client 1 — channel live", "status": "next"},
                {"name": "Invite deep-link onboarding", "status": "next"},
            ],
            "last_progress": {
                "at": "2026-10-27T17:31:33Z",
                "line": "Tenant-provisioner plan approved in shape (op#34565, bus 78463)",
            },
            "seeded_by": CREATED_BY,
            "seed_op": OP_REF,
        },
    },
]


def _phases_column_exists(cur) -> bool:
    cur.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = 'operator_backlog' AND column_name = 'phases'"""
    )
    return cur.fetchone() is not None


def seed(dsn: str) -> int:
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        if not _phases_column_exists(cur):
            conn.rollback()
            raise SystemExit(
                "✗ REFUSE: operator_backlog has no `phases` column — migration "
                "122 is not applied on this store. Apply the migration first "
                "(scripts/apply_migration.py, gated); seeding before it would "
                "create rows the build-status view can never see."
            )

        cur.execute(
            "SELECT count(*) FROM operator_backlog WHERE op_ref = %s", (OP_REF,)
        )
        existing = cur.fetchone()[0]
        if existing == len(PROGRAMMES):
            conn.rollback()
            print(f"✓ already seeded: {existing} programme rows under {OP_REF}")
            return 0
        if existing:
            conn.rollback()
            raise SystemExit(
                f"✗ REFUSE: {existing} programme rows exist under {OP_REF} "
                f"(expected 0 or {len(PROGRAMMES)}) — manual state; inspect "
                f"before re-seeding."
            )

        # New programmes go to the END of the operator's swipe-ordered backlog
        # (sort_order is the primary key of his worklist; lower floats higher).
        cur.execute("SELECT coalesce(max(sort_order), 0) FROM operator_backlog")
        next_sort = cur.fetchone()[0] + 10

        due_at = datetime.now(timezone.utc) + timedelta(days=CHECKPOINT_DAYS)
        for prog in PROGRAMMES:
            phases = dict(prog["phases"])
            phases["next_checkpoint"] = due_at.date().isoformat()
            cur.execute(
                """INSERT INTO operator_backlog (ask, status, op_ref, sort_order, phases)
                   VALUES (%s, 'in_progress', %s, %s, %s) RETURNING id""",
                (prog["ask"], OP_REF, next_sort, json.dumps(phases)),
            )
            backlog_id = cur.fetchone()[0]
            next_sort += 1
            # ONE live checkpoint per programme so programme_stall_guard's
            # invariant (open programme <-> live checkpoint) holds from minute
            # one — a day-one page would be seed noise, not a real stall.
            cur.execute(
                """INSERT INTO held_commitments
                     (owner_agent, title, payload, due_at, status, source_ref, created_by)
                   VALUES (%s, %s, %s, %s, 'pending', %s, %s)""",
                (
                    phases["owner"],
                    f"Programme checkpoint: {prog['ask'][:60]}",
                    json.dumps({"backlog_id": backlog_id}),
                    due_at,
                    OP_REF,
                    CREATED_BY,
                ),
            )

        conn.commit()
        print(
            f"✓ seeded {len(PROGRAMMES)} programme rows + checkpoints "
            f"(next checkpoint {due_at.date().isoformat()}) under {OP_REF}"
        )
        return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL")
    args = p.parse_args(argv)
    dsn = args.dsn or __import__("os").environ.get("DATABASE_URL")
    if not dsn:
        print("✗ no DSN: pass --dsn or set DATABASE_URL", file=sys.stderr)
        return 2
    return seed(dsn)


if __name__ == "__main__":
    sys.exit(main())
