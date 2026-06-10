"""Integration tests for the blocking_tasks reconciliation primitive (BUG-035)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")

from nervous_system.blocking_tasks import (
    create_blocking_task, reconcile_blocking_task, list_open_blocking_tasks,
)


@pytest.fixture
def cleanup_ids():
    ids: list[int] = []
    yield ids
    if not ids:
        return
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "update strategic_decisions set unblocks_task_id = null "
            "where unblocks_task_id = any(%s)", (ids,))
        cur.execute("delete from blocking_tasks where id = any(%s)", (ids,))


def test_create_returns_id_and_lists_as_open(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    assert isinstance(tid, int) and tid > 0
    rows = list_open_blocking_tasks(_DSN, include_test=True)
    assert any(r["id"] == tid and r["status"] == "open" for r in rows)


def test_reconcile_sets_reconciled_state(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    ok = reconcile_blocking_task(_DSN, tid, "CAI-TEST-001")
    assert ok is True
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "select status, reconciled_at is not null, reconciled_by_decision_ref "
            "from blocking_tasks where id = %s", (tid,))
        status, recon_set, ref = cur.fetchone()
    assert status == "reconciled" and recon_set is True and ref == "CAI-TEST-001"


def test_reconciled_task_drops_out_of_open_list(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    reconcile_blocking_task(_DSN, tid, "CAI-TEST-001")
    rows = list_open_blocking_tasks(_DSN, include_test=True)
    assert all(r["id"] != tid for r in rows)


def test_reconcile_is_idempotent(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    assert reconcile_blocking_task(_DSN, tid, "CAI-TEST-001") is True
    assert reconcile_blocking_task(_DSN, tid, "CAI-TEST-002") is True
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "select reconciled_by_decision_ref from blocking_tasks where id = %s",
            (tid,))
        # first reconcile wins; idempotent re-call does not overwrite
        assert cur.fetchone()[0] == "CAI-TEST-001"


def test_reconcile_unknown_id_returns_false():
    assert reconcile_blocking_task(_DSN, 999999999, "CAI-TEST-001") is False


def test_list_excludes_test_by_default(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    rows = list_open_blocking_tasks(_DSN)  # include_test defaults False
    assert all(r["id"] != tid for r in rows)
