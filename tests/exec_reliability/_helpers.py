"""Small DB helpers shared by the exec-reliability self-tests."""
from __future__ import annotations

from typing import Any, Optional

import psycopg


def insert_pending_item(
    dsn: str,
    schema: str,
    *,
    grant_ref: str,
    idempotency_key: str,
    named_artifact: dict,
    lease_scope: list[str],
    state: str = "pending",
    attempts: int = 0,
    max_attempts: int = 5,
    lease_expires_at_sql: str = "null",
    claimed_by: Optional[str] = None,
) -> int:
    """Insert a work-item directly (bypassing enqueue) for runner/relay tests."""
    import json as _json

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            insert into {schema}.exec_work_items
                (grant_ref, consumer_type, named_artifact, idempotency_key,
                 lease_scope, state, attempts, max_attempts, claimed_by,
                 lease_expires_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, {lease_expires_at_sql})
            returning id
            """,
            (grant_ref, named_artifact.get("consumer_type", "relay"),
             _json.dumps(named_artifact), idempotency_key, lease_scope, state,
             attempts, max_attempts, claimed_by),
        )
        return cur.fetchone()[0]


def claim_specific(dsn: str, schema: str, item_id: int, agent_id: str,
                   ttl_seconds: int = 300) -> dict[str, Any]:
    """Claim ONE specific item by id (deterministic per-test isolation)."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            update {schema}.exec_work_items
               set state='claimed', claimed_by=%s, claimed_at=now(),
                   lease_expires_at=now()+make_interval(secs=>%s),
                   attempts=attempts+1
             where id=%s
            returning id, grant_ref, consumer_type, named_artifact,
                      idempotency_key, lease_scope, attempts, max_attempts,
                      claimed_by, state
            """,
            (agent_id, ttl_seconds, item_id),
        )
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, cur.fetchone()))


def fetch_item(dsn: str, schema: str, item_id: int) -> Optional[dict[str, Any]]:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"select * from {schema}.exec_work_items where id = %s", (item_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def count_ledger(dsn: str, schema: str, idempotency_key: str) -> int:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"select count(*) from {schema}.exec_delivery_ledger "
            f"where idempotency_key = %s",
            (idempotency_key,),
        )
        return cur.fetchone()[0]


def max_bus_id(dsn: str) -> int:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select coalesce(max(id), 0) from agent_messages")
        return cur.fetchone()[0]


def bus_rows_since(dsn: str, since_id: int, from_agent: str) -> list[dict]:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "select id, subject, body, message_type from agent_messages "
            "where id > %s and from_agent = %s order by id",
            (since_id, from_agent),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def cleanup_bus(dsn: str, since_id: int, from_agent: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "delete from agent_messages where id > %s and from_agent = %s",
            (since_id, from_agent),
        )
