"""Live-DB tests for BUG-PIPELINE-SYNTHETIC-FILTER-001.

Schema assertions, apply_classification round-trips, boot_briefing
view exposure, backfill verification. All gated on DATABASE_URL —
skip silently in CI without secrets.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


@pytestmark_integration
def test_rejected_at_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name='bug_reports' AND column_name='rejected_at'"
            )
            r = cur.fetchone()
    assert r is not None, "rejected_at column missing"
    assert r[0] == "timestamp with time zone"
    assert r[1] == "YES", "should be nullable"


@pytestmark_integration
def test_rejected_by_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name='bug_reports' AND column_name='rejected_by'"
            )
            r = cur.fetchone()
    assert r is not None, "rejected_by column missing"
    assert r[0] == "text"
    assert r[1] == "YES", "should be nullable"


import asyncio
import json


@pytestmark_integration
def test_shadow_mode_writes_notification_log_does_not_update_bug():
    """In shadow mode, apply_classification logs the classification but
    leaves bug_reports.status untouched."""

    async def _run():
        from nervous_system.synthetic_filter import (
            SyntheticClassification, apply_classification,
        )
        from supabase import acreate_client
        from dotenv import dotenv_values

        # Load directly from .env to avoid autouse set_test_env fixture overwrite
        _env = dotenv_values(Path(__file__).parent.parent / ".env")
        url = _env.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
        key = _env.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
        assert url and key, "SUPABASE_URL + SUPABASE_SERVICE_KEY required for integration tests"
        assert "test.supabase.co" not in url, "Real SUPABASE_URL required, got test stub"
        supabase = await acreate_client(url, key)

        bug_id = str(uuid.uuid4())
        test_marker = f"synthfilter-test-{uuid.uuid4().hex[:8]}"
        insert_resp = await supabase.table("bug_reports").insert({
            "id": bug_id,
            "reporter_name": f"{test_marker} (Test)",
            "reporter_source": "web",
            "auth_provider": "none",
            "repo_name": "cosem-tdu",
            "description": "real text — but flagged via reporter rule b",
            "status": "new",
        }).execute()
        assert insert_resp.data, "test setup: bug insert failed"

        try:
            classification = SyntheticClassification(
                rule="b_test_reporter",
                matched_text=f"{test_marker} (Test)",
            )
            bug = insert_resp.data[0]

            await apply_classification(supabase, bug, classification, mode="shadow")

            check_bug = await supabase.table("bug_reports").select("status, rejected_at").eq("id", bug_id).execute()
            assert check_bug.data[0]["status"] == "new", \
                f"shadow mode must not update status, got {check_bug.data[0]['status']}"
            assert check_bug.data[0]["rejected_at"] is None

            log_check = await supabase.table("notification_log").select("source, message_text").eq("recipient", bug_id).execute()
            assert len(log_check.data) == 1
            entry = log_check.data[0]
            assert entry["source"] == "synthetic_filter"
            msg = json.loads(entry["message_text"])
            assert msg["mode"] == "shadow"
            assert msg["would_reject"] is True
            assert msg["rule"] == "b_test_reporter"
            assert msg["bug_id"] == bug_id
        finally:
            await supabase.table("notification_log").delete().eq("recipient", bug_id).execute()
            await supabase.table("bug_reports").delete().eq("id", bug_id).execute()

    asyncio.run(_run())
