"""blocking_tasks — BUG-035 / CAI-RESP-205 reconciliation primitive.

Cross-agent BLOCKING handoffs need a CHECKED reconciliation state, not a
convention: a ruling can be read yet leave the dependent task un-cleared
(read != reconciled). Each blocker gets a substrate row with an id; rulings
reference it via strategic_decisions.unblocks_task_id; the OWNER agent calls
reconcile_blocking_task when it has actually consumed the ruling. Reconciliation
is NEVER auto-stamped on ruling-existence — that reproduces the exact bug.

Scope (CAI-RESP-205): cross-agent blockers only. Not a general task manager;
local in-session tasks stay local.
"""
from __future__ import annotations

from typing import Any, Optional

import psycopg


def create_blocking_task(
    dsn: str,
    *,
    owner_agent: str,
    created_by: str,
    subject: str,
    detail: Optional[str] = None,
    thread_id: Optional[str] = None,
    is_test: bool = False,
) -> int:
    """Insert a cross-agent blocking task; return its substrate id."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into blocking_tasks "
            "(owner_agent, created_by, subject, detail, thread_id, is_test) "
            "values (%s, %s, %s, %s, %s, %s) returning id",
            (owner_agent, created_by, subject, detail, thread_id, is_test),
        )
        return cur.fetchone()[0]


def reconcile_blocking_task(dsn: str, task_id: int, decision_ref: str) -> bool:
    """Explicit owner close: stamp reconciled_at. Idempotent; first ref wins.

    Returns True if the task is (now or already) reconciled, False for unknown
    or cancelled ids.
    """
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "update blocking_tasks set status = 'reconciled', "
            "reconciled_at = now(), reconciled_by_decision_ref = %s "
            "where id = %s and status = 'open'",
            (decision_ref, task_id),
        )
        if cur.rowcount == 1:
            return True
        # No open row updated: True only if it is already reconciled.
        cur.execute("select status from blocking_tasks where id = %s", (task_id,))
        row = cur.fetchone()
        return bool(row and row[0] == "reconciled")


def list_open_blocking_tasks(
    dsn: str, *, include_test: bool = False
) -> list[dict[str, Any]]:
    """Return open blocking tasks (the blocker view), newest first."""
    sql = (
        "select bt.id, bt.owner_agent, bt.created_by, bt.subject, bt.detail, "
        "bt.thread_id, bt.created_at, bt.is_test, bt.status, "
        "sd.decision_ref as unblocking_ruling_ref, "
        "(sd.decision_ref is not null) as ruling_issued "
        "from blocking_tasks bt "
        "left join strategic_decisions sd on sd.unblocks_task_id = bt.id "
        "where bt.status = 'open'"
    )
    if not include_test:
        sql += " and bt.is_test is not true"
    sql += " order by bt.created_at desc"
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
