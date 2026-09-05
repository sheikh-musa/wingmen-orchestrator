"""CAI-RESP-168 C1: fail-closed R4 arm check.

If the watchdog_monitored_callers arm is missing from boot_briefing, the
long-caller sweep must PAUSE — not run blind. Verified by unit-testing
_check_r4_arm_present's return contract under three conditions.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import legacy.watchdog as watchdog

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def _mock_viewdef(view_def: str):
    """Helper — return a context manager that patches psycopg to yield view_def."""
    pg_mock = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (view_def,)
    pg_mock.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    return pg_mock


def _all_arms_def() -> str:
    """Synthetic viewdef that mentions every arm in the expected set."""
    return " UNION ALL ".join(f"SELECT '{a}'::text AS source ..." for a in watchdog._EXPECTED_BOOT_BRIEFING_ARMS)


class TestCheckR4ArmPresent:
    def test_all_expected_arms_present_returns_true(self):
        with patch.object(watchdog, "psycopg", _mock_viewdef(_all_arms_def())):
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is True
        assert why is None

    def test_watchdog_monitored_callers_arm_missing_returns_false(self):
        # All arms except watchdog_monitored_callers
        defn = " UNION ALL ".join(
            f"SELECT '{a}'::text AS source ..."
            for a in watchdog._EXPECTED_BOOT_BRIEFING_ARMS
            if a != "watchdog_monitored_callers"
        )
        with patch.object(watchdog, "psycopg", _mock_viewdef(defn)):
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is False
        assert "watchdog_monitored_callers" in why

    def test_active_autonomous_loops_arm_missing_returns_false(self):
        """M_DETECTIVE generalizes C1 — any arm regression must pause the watchdog."""
        defn = " UNION ALL ".join(
            f"SELECT '{a}'::text AS source ..."
            for a in watchdog._EXPECTED_BOOT_BRIEFING_ARMS
            if a != "active_autonomous_loops"
        )
        with patch.object(watchdog, "psycopg", _mock_viewdef(defn)):
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is False
        assert "active_autonomous_loops" in why

    def test_long_running_caller_arm_missing_returns_false(self):
        defn = " UNION ALL ".join(
            f"SELECT '{a}'::text AS source ..."
            for a in watchdog._EXPECTED_BOOT_BRIEFING_ARMS
            if a != "long_running_caller"
        )
        with patch.object(watchdog, "psycopg", _mock_viewdef(defn)):
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is False
        assert "long_running_caller" in why

    def test_multiple_arms_missing_lists_all(self):
        """Two arms gone in one regression — reason mentions both."""
        defn = " UNION ALL ".join(
            f"SELECT '{a}'::text AS source ..."
            for a in watchdog._EXPECTED_BOOT_BRIEFING_ARMS
            if a not in ("active_autonomous_loops", "long_running_caller")
        )
        with patch.object(watchdog, "psycopg", _mock_viewdef(defn)):
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is False
        assert "active_autonomous_loops" in why
        assert "long_running_caller" in why

    def test_db_error_fails_closed(self):
        """Any exception talking to Postgres → fails closed (not present)."""
        pg_mock = MagicMock()
        pg_mock.connect.side_effect = RuntimeError("connection refused")
        with patch.object(watchdog, "psycopg", pg_mock):
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is False
        assert "boot_briefing read failed" in why
        assert "connection refused" in why

    def test_expected_arm_set_is_frozenset(self):
        """Drift guard — set must be immutable so a future caller can't mutate the contract."""
        import pytest as _pytest
        with _pytest.raises(AttributeError):
            watchdog._EXPECTED_BOOT_BRIEFING_ARMS.add("evil")  # type: ignore
