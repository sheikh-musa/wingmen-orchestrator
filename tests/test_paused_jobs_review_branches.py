"""Tests for PAUSED-JOBS-RETRY-POLICY-001 AC (ii)+(iii) boot_briefing branches."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


@pytestmark_integration
def test_boot_briefing_has_paused_job_review_needed_branch():
    """AC (ii): boot_briefing view body includes paused_job_review_needed source."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT definition FROM pg_views WHERE viewname='boot_briefing'")
            body = cur.fetchone()[0]
    assert "'paused_job_review_needed'" in body, \
        "boot_briefing missing paused_job_review_needed branch"


@pytestmark_integration
def test_boot_briefing_has_paused_job_permanent_review_branch():
    """AC (iii): boot_briefing view body includes paused_job_permanent_review source."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT definition FROM pg_views WHERE viewname='boot_briefing'")
            body = cur.fetchone()[0]
    assert "'paused_job_permanent_review'" in body, \
        "boot_briefing missing paused_job_permanent_review branch"


@pytestmark_integration
def test_boot_briefing_all_prior_branches_preserved():
    """All 10 prior boot_briefing branches still present after AC (ii)+(iii) rebuild."""
    expected = (
        "repo_context", "repo_snapshot", "active_decision",
        "open_qa_failure", "latest_cc_session", "latest_digest",
        "last_cai_session", "unverified_decisions",
        "manual_override_bugs", "inbox_sla_violation",
        "paused_job_review_needed", "paused_job_permanent_review",
    )
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT definition FROM pg_views WHERE viewname='boot_briefing'")
            body = cur.fetchone()[0]
    missing = [s for s in expected if f"'{s}'" not in body]
    assert not missing, f"boot_briefing missing branches after AC ii+iii rebuild: {missing}"


@pytestmark_integration
def test_paused_job_review_needed_excludes_ghost_success():
    """Ghost-success-prevented rows must NOT appear in review_needed —
    they're routed to permanent_review instead."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM boot_briefing "
                "WHERE source='paused_job_review_needed' "
                "AND context::text LIKE '%ghost success prevented%'"
            )
            assert cur.fetchone()[0] == 0, \
                "review_needed leaked ghost-success-prevented rows (should route to permanent_review)"


@pytestmark_integration
def test_paused_job_permanent_review_only_ghost_success():
    """Inverse: permanent_review only contains ghost-success-prevented rows."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM boot_briefing "
                "WHERE source='paused_job_permanent_review' "
                "AND context::text NOT LIKE '%ghost success prevented%'"
            )
            assert cur.fetchone()[0] == 0, \
                "permanent_review leaked non-ghost rows (should route to review_needed)"


@pytestmark_integration
def test_paused_job_review_needed_classifies_re_paused_allowlist():
    """If a row has the auto-retry marker in result_summary, classification
    field should be 'allowlist_re_paused' (allowlist class that didn't unstick
    after auto-retry — genuine stuck state)."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT context->>'classification' FROM boot_briefing "
                "WHERE source='paused_job_review_needed' "
                "AND context::text LIKE '%paused_jobs_policy auto-retry%'"
            )
            for row in cur.fetchall():
                assert row[0] == "allowlist_re_paused", \
                    f"re-paused allowlist row classified {row[0]!r}, expected 'allowlist_re_paused'"
