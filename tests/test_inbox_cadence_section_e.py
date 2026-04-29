"""Tests for CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 1.

Live-DB integration tests verifying the migration applies the expected shape.
Same pattern as tests/test_deploy_verifier.py (Option B Section 1-5 tests).
"""
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
def test_priority_thresholds_table_exists_with_expected_columns():
    """Section 1: priority_thresholds table has the cadence config shape."""
    expected = {
        "priority":                  ("text", "NO"),
        "sweep_minutes":             ("integer", "NO"),
        "unread_alarm_minutes":      ("integer", "NO"),
        "unresponded_alarm_minutes": ("integer", "NO"),
        "updated_at":                ("timestamp with time zone", "NO"),
        "updated_by":                ("text", "NO"),
    }
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'priority_thresholds'
                   AND column_name = ANY(%s)
                """,
                (list(expected.keys()),),
            )
            actual = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert actual == expected, f"columns mismatch: expected {expected}, got {actual}"


@pytestmark_integration
def test_priority_thresholds_seeded_with_p1_p2_p3():
    """Section C cadence values seeded — P1/P2/P3 with the 15/60/240 cadence."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT priority, sweep_minutes, unread_alarm_minutes, unresponded_alarm_minutes "
                "FROM priority_thresholds ORDER BY priority"
            )
            rows = cur.fetchall()
    assert len(rows) >= 3, f"expected >=3 rows, got {rows}"
    by_pri = {r[0]: r for r in rows}
    assert "P1" in by_pri and "P2" in by_pri and "P3" in by_pri
    # P1: 15-min sweep, alarm 60min unread / 4hr unresponded
    assert by_pri["P1"][1:] == (15, 60, 240), f"P1 thresholds drifted: {by_pri['P1']}"
    # P2: 30-min sweep, 4hr unread / 24hr unresponded
    assert by_pri["P2"][1:] == (30, 240, 1440), f"P2 thresholds drifted: {by_pri['P2']}"
    # P3: 4-hr sweep, 24hr unread / 72hr unresponded
    assert by_pri["P3"][1:] == (240, 1440, 4320), f"P3 thresholds drifted: {by_pri['P3']}"


@pytestmark_integration
def test_priority_thresholds_check_constraints_reject_garbage():
    """CHECK constraints reject invalid priority + non-positive minutes."""
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO priority_thresholds (priority, sweep_minutes, "
                    "unread_alarm_minutes, unresponded_alarm_minutes) VALUES "
                    "('P9', 1, 1, 1)"
                )
        c.rollback()
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO priority_thresholds (priority, sweep_minutes, "
                    "unread_alarm_minutes, unresponded_alarm_minutes) VALUES "
                    "('P1', 0, 1, 1)"
                )
        c.rollback()


@pytestmark_integration
def test_inbox_sla_violations_view_exists():
    """Section 2: inbox_sla_violations view created."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.views WHERE table_name = 'inbox_sla_violations'"
            )
            assert cur.fetchone() is not None, "inbox_sla_violations view missing"


@pytestmark_integration
def test_inbox_sla_violations_returns_expected_columns():
    """View returns the documented column shape."""
    expected_cols = {
        "agent", "message_id", "priority", "from_agent", "subject",
        "created_at", "violation_type", "elapsed_minutes", "threshold_minutes",
    }
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM inbox_sla_violations LIMIT 0")
            actual = {desc.name for desc in cur.description}
    assert expected_cols.issubset(actual), f"missing cols: {expected_cols - actual}"


@pytestmark_integration
def test_inbox_sla_violations_violation_types_in_allowlist():
    """All violation_type values are 'unread' or 'unresponded' — no drift."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT DISTINCT violation_type FROM inbox_sla_violations")
            types = {r[0] for r in cur.fetchall()}
    assert types.issubset({"unread", "unresponded"}), f"unexpected violation_types: {types}"


@pytestmark_integration
def test_inbox_sla_violations_row_passes_threshold():
    """Every surfaced row has elapsed_minutes > threshold_minutes (the predicate)."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT message_id, elapsed_minutes, threshold_minutes, violation_type "
                "FROM inbox_sla_violations WHERE elapsed_minutes <= threshold_minutes LIMIT 5"
            )
            offenders = cur.fetchall()
    assert not offenders, f"rows surfaced below threshold: {offenders}"


@pytestmark_integration
def test_boot_briefing_has_inbox_sla_violation_section():
    """Section 3: boot_briefing rebuild includes inbox_sla_violation branch."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT definition FROM pg_views WHERE viewname = 'boot_briefing'"
            )
            body = cur.fetchone()[0]
    assert "'inbox_sla_violation'::text AS source" in body or "'inbox_sla_violation'" in body, \
        "boot_briefing missing inbox_sla_violation branch"


@pytestmark_integration
def test_boot_briefing_existing_sections_preserved():
    """All 9 prior branches still present after rebuild."""
    expected = (
        "repo_context", "repo_snapshot", "active_decision",
        "open_qa_failure", "latest_cc_session", "latest_digest",
        "last_cai_session", "unverified_decisions",
        "manual_override_bugs", "inbox_sla_violation",
    )
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT definition FROM pg_views WHERE viewname = 'boot_briefing'"
            )
            body = cur.fetchone()[0]
    missing = [s for s in expected if f"'{s}'" not in body]
    assert not missing, f"boot_briefing missing branches: {missing}"
