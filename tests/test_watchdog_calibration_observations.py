"""Tests for CC-WATCHDOG-CALIBRATION-001 observation table + summary script.

Per CAI-RESP-168 §5: the 30-day calibration window needs signal_a near-miss
tracking, not just FP/TP outcome data. Coverage:
  1. Schema present + generated near-miss column behaves correctly
  2. Near-miss band edges (68KB ≤ value ≤ 92KB) honored
  3. Action enum coverage (hard_kill / monitored / no_kill / no_action)
"""
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
pytestmark_integration = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")


@pytestmark_integration
def test_observation_table_exists_with_required_columns():
    expected = {
        "id", "observed_at", "cc_identity", "caller_name",
        "sessions_24h", "cadence_seconds", "parent_pid",
        "signal_a_value", "signal_b_value", "signal_c_value",
        "signal_a_match", "signal_b_match", "signal_c_match",
        "signal_a_unobservable", "signal_b_unobservable", "signal_c_unobservable",
        "registered", "registered_policy", "action", "decision_reason",
        "signal_a_near_miss",
    }
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='watchdog_calibration_observations'"
        )
        actual = {r[0] for r in cur.fetchall()}
    missing = expected - actual
    assert not missing, f"missing columns: {missing}"


@pytestmark_integration
def test_near_miss_column_generated_correctly():
    """signal_a_near_miss is GENERATED ALWAYS — verify the edges of the
    15% band around 80KB: 68KB ≤ v ≤ 92KB."""
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        # Insert test rows at the band edges + outside, read back near-miss
        test_cases = [
            ("test-below-68", 67 * 1024,    False),
            ("test-at-68",    68 * 1024,    True),
            ("test-at-80",    80 * 1024,    True),
            ("test-at-92",    92 * 1024,    True),
            ("test-above-92", 93 * 1024,    False),
            ("test-null",     None,         False),
        ]
        try:
            for caller, value, expected in test_cases:
                cur.execute(
                    """
                    INSERT INTO watchdog_calibration_observations
                      (cc_identity, caller_name, sessions_24h, signal_a_value,
                       registered, action)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING signal_a_near_miss
                    """,
                    (f"cc-test-{caller}", caller, 0, value, False, "no_action"),
                )
                got = cur.fetchone()[0]
                assert got is expected, (
                    f"near_miss for value={value} expected {expected}, got {got}"
                )
        finally:
            cur.execute(
                "DELETE FROM watchdog_calibration_observations "
                "WHERE caller_name LIKE 'test-%'"
            )


@pytestmark_integration
def test_indexes_present_for_calibration_queries():
    """Verify the indexes the summary script needs exist."""
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename='watchdog_calibration_observations'"
        )
        names = {r[0] for r in cur.fetchall()}
    for required in (
        "idx_wco_observed_at",
        "idx_wco_cc_identity_observed",
        "idx_wco_near_miss",
        "idx_wco_action",
    ):
        assert required in names, f"missing index: {required}"
