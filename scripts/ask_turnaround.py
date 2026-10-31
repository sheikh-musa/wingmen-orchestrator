#!/usr/bin/env python3
"""ask_turnaround.py — refresh operator_ask_turnaround (Musa op#34803;
orch-console #78190 plan, approved with one change in #78198).

Idempotent upsert, one row per DELIVERED ask (operator_asks where
delivered_at IS NOT NULL, falling back to closed_at per migration 121's
delivered_source CHECK). For each such ask, scans agent_messages SUBJECTS
only (never body -- #78194 found a false ack from a body mention) inside
the window [asked_at, delivered_at + 1h], excluding orch-console and
cc-fleet-health (the asker and the triager, never a genuine lane ack), for
the first row whose subject matches op#<N>([^0-9]|$) -- that is the
first_ack. Among rows at-or-after that ack matching the same op# AND one of
started|starting|in progress|building (case-insensitive), the first is
first_started (optional -- NULL if no lane ever said it).

surface is derived from the acking lane's family, per the approved plan's
exact mapping (ask_surface is always 'operator' on the row itself, so it's
useless for a per-surface split): cc-cosem-adcda* -> cosem-adcda,
cc-cosem-platform* -> cosem-platform, everything else -> fleet. An ask with
no lane ack at all gets surface='unmatched' (counted, not dropped, per
#78194) -- phases stay NULL, total is still asked->delivered.

Usage:
  ask_turnaround.py --refresh [--dry-run]
    --refresh   upsert every delivered ask into operator_ask_turnaround
    --dry-run   print what would change (counts only), write nothing
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))

_REFRESH_SQL = """
WITH eligible AS (
  SELECT
    oa.id AS ask_id,
    oa.source_msg_id AS op,
    om.created_at AS asked_at,
    COALESCE(oa.delivered_at, oa.closed_at) AS delivered_at,
    CASE WHEN oa.delivered_at IS NOT NULL THEN 'delivered_at' ELSE 'closed_at' END AS delivered_source
  FROM operator_asks oa
  JOIN operator_messages om ON om.id = oa.source_msg_id
  WHERE COALESCE(oa.delivered_at, oa.closed_at) IS NOT NULL
),
first_ack AS (
  SELECT e.ask_id, a.from_agent AS ack_agent, a.created_at AS acked_at
  FROM eligible e
  LEFT JOIN LATERAL (
    SELECT from_agent, created_at
    FROM agent_messages
    WHERE created_at >= e.asked_at
      AND created_at <= e.delivered_at + interval '1 hour'
      AND from_agent NOT IN ('orch-console', 'cc-fleet-health')
      AND from_agent NOT LIKE 'cc-fleet-health-%'
      AND subject ~ ('op#' || e.op::text || '([^0-9]|$)')
    ORDER BY created_at ASC
    LIMIT 1
  ) a ON true
),
first_started AS (
  SELECT e.ask_id, s.created_at AS started_at
  FROM eligible e
  JOIN first_ack fa ON fa.ask_id = e.ask_id AND fa.acked_at IS NOT NULL
  LEFT JOIN LATERAL (
    SELECT created_at
    FROM agent_messages
    WHERE created_at >= fa.acked_at
      AND created_at <= e.delivered_at + interval '1 hour'
      AND from_agent NOT IN ('orch-console', 'cc-fleet-health')
      AND from_agent NOT LIKE 'cc-fleet-health-%'
      AND subject ~ ('op#' || e.op::text || '([^0-9]|$)')
      AND subject ~* '(started|starting|in progress|building)'
    ORDER BY created_at ASC
    LIMIT 1
  ) s ON true
)
INSERT INTO operator_ask_turnaround (
  ask_id, op, surface, ack_agent, asked_at, acked_at, started_at,
  delivered_at, delivered_source, queue_wait, queued_after_ack, build_to_live,
  total, refreshed_at
)
SELECT
  e.ask_id, e.op,
  CASE
    WHEN fa.ack_agent IS NULL THEN 'unmatched'
    WHEN fa.ack_agent LIKE 'cc-cosem-adcda%' THEN 'cosem-adcda'
    WHEN fa.ack_agent LIKE 'cc-cosem-platform%' THEN 'cosem-platform'
    ELSE 'fleet'
  END,
  fa.ack_agent,
  e.asked_at, fa.acked_at, fs.started_at,
  e.delivered_at, e.delivered_source,
  CASE WHEN fa.acked_at IS NOT NULL THEN fa.acked_at - e.asked_at END,
  CASE WHEN fs.started_at IS NOT NULL THEN fs.started_at - fa.acked_at END,
  CASE WHEN fa.acked_at IS NOT NULL THEN e.delivered_at - COALESCE(fs.started_at, fa.acked_at) END,
  e.delivered_at - e.asked_at,
  now()
FROM eligible e
LEFT JOIN first_ack fa ON fa.ask_id = e.ask_id
LEFT JOIN first_started fs ON fs.ask_id = e.ask_id
ON CONFLICT (ask_id) DO UPDATE SET
  op = EXCLUDED.op, surface = EXCLUDED.surface, ack_agent = EXCLUDED.ack_agent,
  asked_at = EXCLUDED.asked_at, acked_at = EXCLUDED.acked_at, started_at = EXCLUDED.started_at,
  delivered_at = EXCLUDED.delivered_at, delivered_source = EXCLUDED.delivered_source,
  queue_wait = EXCLUDED.queue_wait, queued_after_ack = EXCLUDED.queued_after_ack,
  build_to_live = EXCLUDED.build_to_live, total = EXCLUDED.total, refreshed_at = EXCLUDED.refreshed_at
"""

_COUNT_ELIGIBLE_SQL = """
SELECT count(*) FROM operator_asks oa
WHERE COALESCE(oa.delivered_at, oa.closed_at) IS NOT NULL
"""


def _dsn() -> "str | None":
    """Same fallback shape as asks_triage.py's _dsn()."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if dsn:
        return dsn
    load_dotenv(ORCH / ".env")
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def refresh(conn) -> int:
    """Idempotent upsert of every delivered ask. Returns the row count
    touched (eligible asks), not the digest numbers -- call
    operator_ask_turnaround_daily for those."""
    with conn.cursor() as cur:
        cur.execute(_COUNT_ELIGIBLE_SQL)
        (eligible,) = cur.fetchone()
        cur.execute(_REFRESH_SQL)
    conn.commit()
    return eligible


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="upsert every delivered ask")
    ap.add_argument("--dry-run", action="store_true", help="count eligible rows, write nothing")
    args = ap.parse_args(argv)

    if not args.refresh and not args.dry_run:
        ap.error("pass --refresh or --dry-run")

    dsn = _dsn()
    if not dsn:
        print("ask_turnaround: no DATABASE_URL/SUPABASE_DB_URL", file=sys.stderr)
        return 2

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        if args.dry_run:
            with conn.cursor() as cur:
                cur.execute(_COUNT_ELIGIBLE_SQL)
                (eligible,) = cur.fetchone()
            print(f"ask_turnaround: {eligible} delivered ask(s) eligible (dry-run, no write)")
            return 0
        eligible = refresh(conn)
    print(f"ask_turnaround: refreshed, {eligible} delivered ask(s) upserted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
