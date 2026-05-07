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


@pytestmark_integration
def test_enforce_mode_writes_log_and_rejects_bug():
    """In enforce mode, apply_classification logs AND sets status='rejected'
    + rejection_reason + rejected_at + rejected_by."""

    async def _run():
        from nervous_system.synthetic_filter import (
            SyntheticClassification, apply_classification,
        )
        from supabase import acreate_client
        from dotenv import dotenv_values

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
            "description": "test",
            "status": "new",
        }).execute()
        assert insert_resp.data

        try:
            classification = SyntheticClassification(
                rule="b_test_reporter",
                matched_text=f"{test_marker} (Test)",
            )
            bug = insert_resp.data[0]

            await apply_classification(supabase, bug, classification, mode="enforce")

            check = await supabase.table("bug_reports").select(
                "status, rejection_reason, rejected_at, rejected_by"
            ).eq("id", bug_id).execute()
            row = check.data[0]
            assert row["status"] == "rejected"
            assert row["rejection_reason"] == "synthetic_e2e_test"
            assert row["rejected_at"] is not None
            assert row["rejected_by"] == "cc-orchestrator-filter"

            log_check = await supabase.table("notification_log").select("message_text").eq("recipient", bug_id).execute()
            msg = json.loads(log_check.data[0]["message_text"])
            assert msg["mode"] == "enforce"
        finally:
            await supabase.table("notification_log").delete().eq("recipient", bug_id).execute()
            await supabase.table("bug_reports").delete().eq("id", bug_id).execute()

    asyncio.run(_run())


@pytestmark_integration
def test_backfill_flipped_existing_synthetic_to_rejected():
    """Section 2 backfill: rows matching cai a/b OR PR #28 is_test=true,
    in non-terminal status, must now have status='rejected' + audit fields."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM bug_reports
                 WHERE rejected_by = 'cc-orchestrator-filter-backfill'
                   AND rejection_reason = 'synthetic_e2e_test'
                   AND status = 'rejected'
            """)
            backfilled = cur.fetchone()[0]
    assert backfilled > 0, (
        "expected backfill to flip at least one historical row "
        "(cc-x-e2e + (Test) reporters known to exist)"
    )


@pytestmark_integration
def test_backfill_did_not_touch_terminal_rows():
    """Backfill must not flip already-rejected/deployed/verified rows."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM bug_reports
                 WHERE status IN ('verified', 'deployed')
                   AND rejected_by IS NOT NULL
            """)
            leaked = cur.fetchone()[0]
    assert leaked == 0, f"{leaked} terminal rows incorrectly tagged with rejected_by"


@pytestmark_integration
def test_boot_briefing_view_has_synthetic_filter_arms():
    """View definition should reference synthetic_filter / filtered_24h /
    shadow_24h after the migration."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "synthetic_filter" in defn, "boot_briefing view missing synthetic_filter source"
    assert "filtered_24h" in defn, "boot_briefing view missing filtered_24h key"
    assert "shadow_24h" in defn, "boot_briefing view missing shadow_24h key"


@pytestmark_integration
def test_boot_briefing_synthetic_filter_zero_count_omitted():
    """When no synthetic_filter notification_log entries exist in 24h,
    the boot_briefing view rows should be ABSENT (HAVING count > 0)."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                DELETE FROM notification_log
                 WHERE source='synthetic_filter'
                   AND created_at >= now() - interval '24 hours'
            """)
            cur.execute("""
                SELECT key FROM boot_briefing
                 WHERE source = 'synthetic_filter'
            """)
            keys = [r[0] for r in cur.fetchall()]
    assert keys == [], f"expected no synthetic_filter rows when log empty, got {keys}"
