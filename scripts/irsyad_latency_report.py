#!/usr/bin/env python3
"""irsyad_latency_report.py — how fast does cc-irsyad answer, vs the hub?

Both responders work the same durable log, so the comparison needs no new plumbing:
    inbound client message (tag 'gazzabyte-irsyad')
      -> hub's SENT reply        (outbound, tag 'gazzabyte-irsyad')
      -> cc-irsyad's DRAFT       (outbound, tag 'gazzabyte-irsyad-draft', from_name 'cc-irsyad')
      -> cc-irsyad's SENT reply  (outbound, tag 'gazzabyte-irsyad', from_name 'cc-irsyad')

While the lane is on the 'supervised' phase its output is the DRAFT column — that is the fair
comparison to the hub's send (both are "time to a client-ready answer"; the human review step
is measured separately as draft -> send). After cutover the lane's own SENT column takes over.

Usage: scripts/irsyad_latency_report.py [--hours 48]
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg.rows import dict_row

CLIENT_TAG = "gazzabyte-irsyad"
DRAFT_TAG = "gazzabyte-irsyad-draft"

# A response counts for a message only if it lands BEFORE the next client message (and
# within CAP hours). Without that window every later reply gets credited to every earlier
# message, which silently invents latencies of days — the first draft of this report did
# exactly that and made the comparison meaningless.
QUERY = """
WITH inbound AS (
    SELECT id, created_at,
           lead(created_at) OVER (ORDER BY created_at) AS next_at,
           left(regexp_replace(text, '\\s+', ' ', 'g'), 60) AS preview
    FROM operator_messages
    WHERE direction='inbound' AND tag=%(tag)s
      AND created_at > now() - make_interval(hours => %(hours)s)
), win AS (
    SELECT id, created_at, preview,
           least(coalesce(next_at, 'infinity'::timestamptz),
                 created_at + make_interval(hours => %(cap)s)) AS until
    FROM inbound
)
SELECT w.id, w.created_at, w.preview,
       (SELECT min(o.created_at) FROM operator_messages o
         WHERE o.direction='outbound' AND o.tag=%(tag)s
           AND o.created_at > w.created_at AND o.created_at < w.until
           AND coalesce(o.from_name,'') <> 'cc-irsyad')            AS hub_sent_at,
       (SELECT min(o.created_at) FROM operator_messages o
         WHERE o.direction='outbound' AND o.tag=%(draft)s
           AND o.created_at > w.created_at AND o.created_at < w.until) AS lane_draft_at,
       (SELECT min(o.created_at) FROM operator_messages o
         WHERE o.direction='outbound' AND o.tag=%(tag)s
           AND o.created_at > w.created_at AND o.created_at < w.until
           AND o.from_name='cc-irsyad')                             AS lane_sent_at
FROM win w
ORDER BY w.id
"""


def mins(a, b) -> str:
    if not a or not b:
        return "     —"
    return f"{(b - a).total_seconds() / 60:6.1f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--cap-hours", type=int, default=6,
                    help="max window to credit a response to a message")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.exit("DATABASE_URL not set")

    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(QUERY, {"tag": CLIENT_TAG, "draft": DRAFT_TAG,
                            "hours": args.hours, "cap": args.cap_hours})
        rows = cur.fetchall()

    if not rows:
        print(f"no client messages on '{CLIENT_TAG}' in the last {args.hours}h")
        return

    print(f"irsyad response latency — last {args.hours}h (minutes from client message)\n")
    print(f"{'id':>6}  {'received (UTC)':<20} {'hub sent':>8} {'lane draft':>10} "
          f"{'lane sent':>10}  message")
    print("-" * 100)
    hub_deltas, lane_deltas = [], []
    for r in rows:
        if r["hub_sent_at"]:
            hub_deltas.append((r["hub_sent_at"] - r["created_at"]).total_seconds() / 60)
        if r["lane_draft_at"]:
            lane_deltas.append((r["lane_draft_at"] - r["created_at"]).total_seconds() / 60)
        print(f"{r['id']:>6}  {r['created_at']:%Y-%m-%d %H:%M:%S}  "
              f"{mins(r['created_at'], r['hub_sent_at'])} "
              f"{mins(r['created_at'], r['lane_draft_at'])} "
              f"{mins(r['created_at'], r['lane_sent_at'])}  {r['preview']}")

    def summarize(name: str, vals: list[float]) -> None:
        if not vals:
            print(f"{name:<12} no responses in window")
            return
        s = sorted(vals)
        print(f"{name:<12} n={len(s):<3} median {s[len(s)//2]:6.1f} min   "
              f"fastest {s[0]:6.1f}   slowest {s[-1]:6.1f}")

    print("\nsummary (time to a client-ready answer)")
    summarize("hub:", hub_deltas)
    summarize("cc-irsyad:", lane_deltas)
    if hub_deltas and lane_deltas:
        h = sorted(hub_deltas)[len(hub_deltas) // 2]
        l = sorted(lane_deltas)[len(lane_deltas) // 2]
        faster = "cc-irsyad" if l < h else "the hub"
        print(f"\nmedian: {faster} is faster by {abs(h - l):.1f} min "
              f"({'lane' if l < h else 'hub'} {min(h, l):.1f} vs {max(h, l):.1f})")
    print("\nnote: 'lane draft' is the supervised-phase output (Nazim sends it); "
          "'lane sent' only fills in after direct cutover.")


if __name__ == "__main__":
    main()
