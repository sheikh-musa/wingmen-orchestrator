"""Enqueue: decision -> durable work-item (spec §2).

The GRANT is the ONLY thing that creates authorized work (EXEC-1). This module
watches strategic_decisions for rows that have been flipped to
`execution_status='granted'` with the challenge window CLOSED
(`challenge_status='accepted'`), and drains each onto exactly one exec_work_item.

CRITICAL: this module NEVER writes strategic_decisions and NEVER writes
execution_status. It only READS granted decisions and INSERTs work-items (as the
service-role poller). It grants nothing.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import psycopg

from ._common import compute_lease_scope, make_idempotency_key, qualified
from .schema import GRANTED

# challenge window "closed" == accepted (mirrors strategic_decisions_poll.py).
CHALLENGE_CLOSED = ("accepted", "accepted_by_timeout")

# MVP: the granting artifact must name which consumer executes it. We only wire
# 'relay' in the MVP; other kinds are enqueued but will fail-closed BOUNCE at the
# runner if no handler is registered (never silently improvised).
DEFAULT_CONSUMER_TYPE = "relay"


ARTIFACT_PREFIX = "exec_artifact:"


def _artifact_of(decision: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Extract the EXACT named artifact from a granted decision.

    MVP contract: strategic_decisions.constraints is a text[]. The granting path
    carries the artifact as one element `exec_artifact:<json-object>`. No such
    element -> not drainable (we do NOT invent one; that would be improvising
    authorized work).
    """
    raw = decision.get("constraints")
    if not raw:
        return None
    elements = raw if isinstance(raw, (list, tuple)) else [raw]
    for el in elements:
        if isinstance(el, str) and el.startswith(ARTIFACT_PREFIX):
            try:
                art = json.loads(el[len(ARTIFACT_PREFIX):])
            except (json.JSONDecodeError, TypeError):
                return None
            return art if isinstance(art, dict) else None
    return None


def enqueue_decision(
    dsn: str,
    decision_ref: str,
    *,
    schema: str = "public",
) -> Optional[int]:
    """Drain a single granted+closed decision onto a work-item.

    Idempotent (EXEC-2): the UNIQUE idempotency_key means a second call for the
    same grant+artifact is a no-op (returns the existing id). Returns the
    work-item id, or None if the decision is not drainable.
    """
    items_t = qualified(schema, "exec_work_items")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select decision_ref, execution_status, challenge_status, status,
                   repos_affected, constraints
            from public.strategic_decisions
            where decision_ref = %s
            """,
            (decision_ref,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        decision = dict(zip(cols, row))

        # EXEC-1 gate: only a GRANTED + challenge-CLOSED decision authorizes work.
        if decision["execution_status"] != GRANTED:
            return None
        if decision["challenge_status"] not in CHALLENGE_CLOSED:
            return None
        if decision["status"] in ("superseded", "archived"):
            return None

        artifact = _artifact_of(decision)
        if artifact is None:
            return None

        consumer_type = artifact.get("consumer_type", DEFAULT_CONSUMER_TYPE)
        # EXEC-4 / CAI-464 c3: an explicit, non-empty authority scope is mandatory.
        lease_scope = compute_lease_scope(decision.get("repos_affected"), artifact)
        if not lease_scope:
            # Cannot name a scope -> refuse to enqueue (never mint unscoped work).
            return None

        idem = make_idempotency_key(decision_ref, consumer_type, artifact)
        cur.execute(
            f"""
            insert into {items_t}
                (grant_ref, consumer_type, named_artifact, idempotency_key,
                 lease_scope, state)
            values (%s, %s, %s, %s, %s, 'pending')
            on conflict (idempotency_key) do nothing
            returning id
            """,
            (
                decision_ref,
                consumer_type,
                json.dumps(artifact),
                idem,
                lease_scope,
            ),
        )
        inserted = cur.fetchone()
        if inserted:
            return inserted[0]
        # Already enqueued (idempotent no-op): return the existing id.
        cur.execute(
            f"select id from {items_t} where idempotency_key = %s", (idem,)
        )
        existing = cur.fetchone()
        return existing[0] if existing else None


def enqueue_granted(dsn: str, *, schema: str = "public") -> list[int]:
    """Poller: drain ALL currently granted+closed decisions (spec §2).

    Returns the list of work-item ids that exist for the drained grants. Safe to
    run on a timer; idempotent via the UNIQUE key.
    """
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select decision_ref
            from public.strategic_decisions
            where execution_status = %s
              and challenge_status = any(%s)
              and status not in ('superseded','archived')
            order by decided_at asc
            """,
            (GRANTED, list(CHALLENGE_CLOSED)),
        )
        refs = [r[0] for r in cur.fetchall()]
    ids: list[int] = []
    for ref in refs:
        wid = enqueue_decision(dsn, ref, schema=schema)
        if wid is not None:
            ids.append(wid)
    return ids


if __name__ == "__main__":  # pragma: no cover - operational entrypoint (hub-wired)
    from dotenv import load_dotenv

    load_dotenv(os.path.expanduser("~/wingmen/orchestrator/.env"))
    _dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    print("enqueued/existing work-item ids:", enqueue_granted(_dsn))
