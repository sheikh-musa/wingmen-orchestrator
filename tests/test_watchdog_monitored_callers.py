"""Live-DB schema test for watchdog_monitored_callers table + boot_briefing arm."""
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
def test_watchdog_monitored_callers_table_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='watchdog_monitored_callers' ORDER BY ordinal_position"
            )
            cols = {r[0]: r[1] for r in cur.fetchall()}
    for required in (
        "caller_name", "first_observed_at", "expires_at",
        "sessions_24h", "cadence_seconds",
        "signal_a_value", "signal_b_value", "signal_c_value",
        "signal_a_match", "signal_b_match", "signal_c_match",
        "escalated_at",
    ):
        assert required in cols, f"missing column: {required}"


@pytestmark_integration
def test_boot_briefing_view_has_watchdog_monitored_callers_arm():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "'watchdog_monitored_callers'::text" in defn, "missing arm"
    # Regression guard against silent rollback (CC-SUBSTRATE-VIEW-INTEGRITY-001 incident).
    for arm in (
        "'repo_context'", "'active_decision'", "'active_autonomous_loops'",
        "'long_running_caller'",
    ):
        assert arm in defn, f"arm {arm} dropped"
