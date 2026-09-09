#!/usr/bin/env python3
"""unreleased_client_drafts.py — the ONE definition of "a supervised client reply was
drafted but never released."

Shared by the self_recycle pre-flight gate (scripts/self_recycle.sh) and the periodic
scripts/supervised_draft_deadman.py so the two can never drift — a control that needs
remembering is a sentence; this is the sentence, in code.

WHY IT EXISTS (2026-09-09 gazzabyte-irsyad strand): a supervised lane files its client
reply as a DRAFT and a reviewer must SEND it (lane_reply.sh supervised phase logs
operator_messages tag '<channel>-draft', delivered=false, and files a review_request to
the reviewer). On 2026-09-09 cc-irsyad-coord drafted Wan's answers, the prior console body
self-recycled before releasing them, and the client (Gazzabyte/Wan) heard nothing for ~11h
until Musa caught it. bus-approving != delivery; the reviewer must SEND — and if the
reviewer vanishes, the draft is orphaned silently. This detector makes that orphan visible.

DETECTION. For each channel currently in the 'supervised' phase, an UNRELEASED draft is a
'<channel>-draft' row (direction outbound, delivered=false) whose created_at is NEWER than
the most recent DELIVERED real outbound to '<channel>'. Rationale: a real send AFTER the
draft means the reviewer answered the thread — the exact text need not match, because one
consolidated reviewer reply can release several drafts at once (op#19450 released three).
If NO real send followed the draft, the client is still waiting on it.

min_age_s filters out drafts younger than the SLA: the deadman passes ~1800s so a
just-filed draft isn't paged before a human could plausibly release it; the recycle GATE
passes 0 — at recycle time ANY unreleased draft must block, because the body about to
vanish is the one that would have released it.

FAIL-CLOSED: a query/connection failure raises CouldNotMeasure. Callers must treat an
inability to measure as "assume there IS an orphan" (the recycle gate refuses; the deadman
pages) — a safety check that cannot read must never read green.
"""
from __future__ import annotations

import os
import re
import sys
from typing import List, Dict, Optional

import psycopg2


class CouldNotMeasure(RuntimeError):
    """Raised when the detector cannot complete its query — callers fail closed."""


def _dsn() -> str:
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    # Read from the orchestrator .env without importing dotenv (find_dotenv breaks under -c).
    path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    try:
        m = re.search(r"^DATABASE_URL=(.+)$", open(path).read(), re.M)
    except OSError as e:
        raise CouldNotMeasure(f"cannot read .env for DATABASE_URL: {e}") from e
    if not m:
        raise CouldNotMeasure("DATABASE_URL not found in env or .env")
    return m.group(1).strip()


# One query, parameterised by the SLA age. Kept here (not duplicated in two callers) so the
# definition of "orphaned draft" has exactly one home.
_QUERY = """
WITH sup AS (
    SELECT channel_tag
    FROM bot_channels
    WHERE group_routing->>'agent_phase' = 'supervised'
      AND channel_tag IS NOT NULL
)
SELECT s.channel_tag,
       d.id,
       d.created_at,
       EXTRACT(EPOCH FROM (now() - d.created_at))::bigint AS age_s,
       coalesce(d.from_name, '') AS drafted_by,
       coalesce(
           (SELECT gr.group_routing->>'agent_reviewer'
              FROM bot_channels gr WHERE gr.channel_tag = s.channel_tag),
           'orch-console') AS reviewer
FROM sup s
JOIN operator_messages d
      ON d.tag = s.channel_tag || '-draft'
     AND d.direction = 'outbound'
     AND d.delivered = false
WHERE d.created_at > COALESCE(
        (SELECT max(o.created_at)
           FROM operator_messages o
          WHERE o.tag = s.channel_tag
            AND o.direction = 'outbound'
            AND o.delivered = true),
        '1970-01-01'::timestamptz)
  AND d.created_at <= now() - make_interval(secs => %(min_age_s)s)
ORDER BY d.created_at ASC
"""


def find_unreleased_drafts(min_age_s: int = 0, dsn: Optional[str] = None) -> List[Dict]:
    """Return unreleased supervised client drafts older than min_age_s.

    Each dict: {channel, draft_id, created_at, age_s, drafted_by, reviewer}.
    Raises CouldNotMeasure on any DB error (callers fail closed).
    """
    dsn = dsn or _dsn()
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(_QUERY, {"min_age_s": int(min_age_s)})
            rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — any failure is a could-not-measure
        raise CouldNotMeasure(str(e)) from e
    return [
        {
            "channel": r[0],
            "draft_id": r[1],
            "created_at": r[2],
            "age_s": int(r[3]),
            "drafted_by": r[4],
            "reviewer": r[5],
        }
        for r in rows
    ]


def _fmt(d: Dict) -> str:
    mins = d["age_s"] // 60
    return (f"  {d['channel']}: draft op#{d['draft_id']} unreleased for {mins}m "
            f"(drafted_by={d['drafted_by'] or '?'}, reviewer={d['reviewer']})")


def main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="detect unreleased supervised client drafts")
    ap.add_argument("--min-age-s", type=int, default=0,
                    help="ignore drafts younger than this (deadman SLA; gate uses 0)")
    args = ap.parse_args(argv)
    try:
        rows = find_unreleased_drafts(min_age_s=args.min_age_s)
    except CouldNotMeasure as e:
        # Exit 2 = could-not-measure. Callers fail CLOSED on this, never green.
        print(f"COULD-NOT-MEASURE unreleased-draft check: {e}", file=sys.stderr)
        return 2
    if not rows:
        print("clear — no unreleased supervised client drafts")
        return 0
    print(f"UNRELEASED supervised client drafts: {len(rows)}")
    for d in rows:
        print(_fmt(d))
    # Exit 1 = orphaned draft(s) found.
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
