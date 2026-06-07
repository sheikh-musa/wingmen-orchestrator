"""Poll cai's agent_messages inbox.

Per CAI-RESP-185 amendment 1: Realtime is a later upgrade. Phase 1 uses
the 5-min poll fallback. Subscribes to no Supabase Realtime channel —
this is a straight SELECT every POLL_INTERVAL_SECONDS.
"""
from __future__ import annotations

import logging
import os
from typing import AsyncIterator

import psycopg

logger = logging.getLogger("cc_cai.poller")

POLL_INTERVAL_SECONDS = 300  # 5min per CAI-RESP-185 amendment 1
_BATCH_LIMIT = 50            # max messages processed per cycle (safety bound)
# Escalation backoff: an unread message that was escalated within this window
# is skipped by the poller. Without this the daemon re-pushes every cycle —
# the regression that flooded the operator on 2026-06-07 first cutover.
ESCALATION_BACKOFF_HOURS = 24


def fetch_unread_for_cai(dsn: str) -> list[dict]:
    """One synchronous fetch of cai's unread inbox.

    Filters: to_agent='cai' AND read_at IS NULL AND is_test=false
    AND skipped_at IS NULL AND no recent escalation audit row.
    The last clause prevents the per-cycle re-push loop: escalation does
    not mark the message read, so without a backoff filter the same row
    would re-classify-and-push every POLL_INTERVAL_SECONDS until operator
    acts. The reminder cycle still fires after ESCALATION_BACKOFF_HOURS.

    Ordered priority-first then created_at ASC so urgent traffic drains first.
    Bounded at _BATCH_LIMIT to keep one cycle tractable.
    """
    sql = (
        "SELECT id, thread_id, from_agent, to_agent, message_type, "
        "       subject, body, requires_response, priority, created_at, "
        "       sub_tag "
        "  FROM agent_messages am "
        " WHERE to_agent = 'cai' "
        "   AND read_at IS NULL "
        "   AND is_test = false "
        "   AND skipped_at IS NULL "
        "   AND NOT EXISTS ("
        "       SELECT 1 FROM cc_cai_audit_log "
        "        WHERE agent_message_id = am.id "
        "          AND event_type = 'escalation' "
        "          AND logged_at > now() - (%s || ' hours')::interval) "
        " ORDER BY priority ASC, created_at ASC "
        " LIMIT %s"
    )
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql, (ESCALATION_BACKOFF_HOURS, _BATCH_LIMIT))
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return rows
    except Exception as e:
        logger.error(f"fetch_unread_for_cai failed: {e}")
        return []
