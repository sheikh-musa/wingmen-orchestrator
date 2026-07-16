"""Demoted watchdog: SAFETY NET, not primary recovery (spec §4).

The old babysitting watchdogs (`nervous_system/lane_watchdog.py`
IDLE_UNSENT-submit + re-entry-nudge; `nervous_system/agent_watchdog.py` check-in
silence) were the PRIMARY path that authorized work got picked up — which is
exactly the fragile pickup/delivery mechanism the durable queue replaces. Under
this layer they demote to a SAFETY NET with two narrow jobs, and no more:

  (a) RE-ENQUEUE on runner death — a 'claimed'/'running' item whose lease expired
      past a grace ttl is returned to 'pending' so a fresh disposable runner can
      reclaim it. (The claim query already reclaims expired leases; this reaper
      also normalises the row + records the reap so a stuck item is visible.)

  (b) ALERT the hub on genuinely stuck items — state unchanged beyond a threshold,
      or attempts exhausted (attempts >= max_attempts) — and then STOP. NO
      infinite auto-retry; a stuck item becomes a hub decision, never a loop.

This module does NOT decide, execute, or nudge tmux panes. It only re-queues dead
work and raises hands.
"""
from __future__ import annotations

import os
from typing import Any

import psycopg

from ._common import post_bus, qualified

# Grace beyond lease expiry before we treat a claimed item as a dead runner's.
DEFAULT_REAP_GRACE_SECONDS = 60
# How long an item may sit stuck (no state change) before we alert the hub.
DEFAULT_STUCK_THRESHOLD_SECONDS = 1800  # 30 min


def reap_expired_leases(
    dsn: str,
    *,
    grace_seconds: int = DEFAULT_REAP_GRACE_SECONDS,
    schema: str = "public",
) -> list[int]:
    """(a) Return dead-runner items to 'pending'. Returns reaped item ids.

    Only touches items still within their retry budget; an item that has
    exhausted max_attempts is left for the stuck-item alert (b), not re-queued —
    this is the "no infinite auto-retry" boundary.
    """
    items_t = qualified(schema, "exec_work_items")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            update {items_t}
               set state = 'pending',
                   claimed_by = null,
                   claimed_at = null,
                   lease_expires_at = null,
                   last_error = coalesce(last_error, '') ||
                       ' [safety-net: reaped dead lease -> pending]'
             where state in ('claimed','running')
               and lease_expires_at is not null
               and lease_expires_at < now() - make_interval(secs => %s)
               and attempts < max_attempts
            returning id
            """,
            (grace_seconds,),
        )
        reaped = [r[0] for r in cur.fetchall()]
    return reaped


def alert_stuck_items(
    dsn: str,
    *,
    stuck_threshold_seconds: int = DEFAULT_STUCK_THRESHOLD_SECONDS,
    from_agent: str = os.environ.get("EXEC_RUNNER_AGENT_ID", "cc-exec-runner"),
    schema: str = "public",
) -> list[dict[str, Any]]:
    """(b) Alert the hub about stuck / exhausted items. NO auto-retry.

    Stuck = attempts exhausted (dead-lettered), OR unchanged for too long. One
    P1 bus row per stuck item so the hub can adjudicate. Returns the stuck rows.
    """
    items_t = qualified(schema, "exec_work_items")
    stuck: list[dict[str, Any]] = []
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            select id, grant_ref, consumer_type, state, attempts, max_attempts,
                   last_error, updated_at
            from {items_t}
            where (
                    (attempts >= max_attempts and state not in ('done','skipped'))
                 or (state in ('claimed','running','pending')
                     and updated_at < now() - make_interval(secs => %s))
                  )
              and state not in ('done','skipped')
            order by updated_at asc
            """,
            (stuck_threshold_seconds,),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        for row in rows:
            exhausted = row["attempts"] >= row["max_attempts"]
            post_bus(
                cur,
                from_agent=from_agent,
                to_agent="cc-orchestrator",
                subject=f"exec STUCK: work_item {row['id']} ({row['grant_ref']})",
                body=(
                    f"Safety-net alert. state={row['state']} "
                    f"attempts={row['attempts']}/{row['max_attempts']} "
                    f"last_error={row['last_error']!r}\n"
                    + ("Attempts EXHAUSTED — dead-lettered, NO auto-retry. "
                       "Hub decision required."
                       if exhausted else
                       "No progress past threshold. Hub, please inspect.")
                ),
                message_type="blocker",
                priority="P1",
                requires_response=True,
            )
            stuck.append(row)
    return stuck
