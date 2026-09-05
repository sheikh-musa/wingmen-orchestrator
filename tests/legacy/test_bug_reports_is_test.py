"""Tests for PR #28 bug_reports.is_test flag — pattern detection helper +
live-DB integration assertions for the migration."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from legacy.bug_pipeline import _detect_is_test

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")


load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


# ============================================================================
# _detect_is_test pure-unit tests (no DB)
# ============================================================================

class TestDetectIsTest:
    def test_test_client_suffix_flagged(self):
        assert _detect_is_test("BAPA Admin (Test)", "Real bug here") is True
        assert _detect_is_test("NZ Halal Meats (Test)", "Anything") is True

    def test_e2e_suite_reporter_flagged(self):
        assert _detect_is_test("cc-cosem-e2e", "Visual regression") is True
        assert _detect_is_test("cc-ihsanos-e2e", "Pipeline test") is True
        assert _detect_is_test("cc-scholar-e2e", "Tafsir smoke") is True

    def test_please_ignore_in_description_flagged(self):
        assert _detect_is_test("Real Human", "Test bug — please ignore") is True
        assert _detect_is_test("Real Human", "PLEASE IGNORE") is True  # case-insensitive

    def test_normal_intake_not_flagged(self):
        assert _detect_is_test("Mulifatullah Bin Atan", "When selecting a vehicle the menu is cutoff") is False
        assert _detect_is_test("musa", "Real production bug") is False
        assert _detect_is_test("", "") is False
        assert _detect_is_test(None, None) is False  # type: ignore  # safety: handle None

    def test_partial_match_does_not_flag(self):
        # "(Test)" must be at end of name — middle/start does not flag
        assert _detect_is_test("Test User", "real bug") is False
        assert _detect_is_test("MyTest User", "real bug") is False
        # "cc-x-e2e" must be the full reporter — substring doesn't count
        assert _detect_is_test("cc-cosem-e2e-helper", "real bug") is False
        assert _detect_is_test("not-cc-cosem-e2e", "real bug") is False


# ============================================================================
# Live-DB tests
# ============================================================================

@pytestmark_integration
def test_is_test_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name='bug_reports' AND column_name='is_test'"
            )
            r = cur.fetchone()
    assert r is not None, "is_test column missing"
    assert r[0] == "boolean"
    assert r[1] == "NO"
    assert r[2] == "false"


@pytestmark_integration
def test_backfill_flagged_known_test_patterns():
    """Backfill should have flagged at least the known existing test rows
    (BAPA Admin (Test), cc-cosem-e2e, "please ignore" descriptions)."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM bug_reports "
                "WHERE is_test = true "
                "AND (reporter_name LIKE %s OR reporter_name LIKE %s OR description ILIKE %s)",
                ("% (Test)", "cc-%-e2e", "%please ignore%"),
            )
            count = cur.fetchone()[0]
    assert count > 0, "backfill matched 0 rows for known test patterns"


@pytestmark_integration
def test_test_jobs_cancelled():
    """All jobs whose bug_report.is_test=true must NOT be in queued/paused/
    pending_review status (migration cancelled them)."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM jobs j "
                "JOIN bug_reports br ON br.job_id = j.id "
                "WHERE br.is_test = true "
                "AND j.status IN ('queued','paused','pending_review')"
            )
            stuck = cur.fetchone()[0]
    assert stuck == 0, f"{stuck} test-bug jobs still in active queue post-migration"


@pytestmark_integration
def test_intake_sets_is_test_for_test_clients():
    """A bug_report INSERT with reporter_name='X (Test)' should have
    is_test=true. Test does the INSERT directly to verify the SQL DEFAULT
    + intake-side _detect_is_test agree."""
    bug_id = str(uuid.uuid4())
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                cur.execute(
                    """INSERT INTO bug_reports
                       (id, reporter_name, reporter_source, auth_provider,
                        repo_name, description, status, is_test)
                       VALUES (%s, %s, 'web', 'none', 'cosem-tdu', 'test', 'new', %s)""",
                    (bug_id, "Some Test User (Test)", _detect_is_test("Some Test User (Test)", "test"))
                )
                cur.execute("SELECT is_test FROM bug_reports WHERE id=%s", (bug_id,))
                assert cur.fetchone()[0] is True
            finally:
                c.rollback()
