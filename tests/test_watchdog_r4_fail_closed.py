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

import watchdog


class TestCheckR4ArmPresent:
    def test_arm_present_returns_true(self):
        fake_defn = (
            " SELECT 'repo_context'::text AS source ... "
            "UNION ALL SELECT 'watchdog_monitored_callers'::text AS source ... "
        )
        with patch.object(watchdog, "psycopg") as pg_mock:
            cur = MagicMock()
            cur.fetchone.return_value = (fake_defn,)
            pg_mock.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is True
        assert why is None

    def test_arm_missing_returns_false(self):
        fake_defn = (
            " SELECT 'repo_context'::text AS source ... "
            "UNION ALL SELECT 'active_autonomous_loops'::text AS source ... "
            # NO watchdog_monitored_callers arm
        )
        with patch.object(watchdog, "psycopg") as pg_mock:
            cur = MagicMock()
            cur.fetchone.return_value = (fake_defn,)
            pg_mock.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is False
        assert "watchdog_monitored_callers" in why

    def test_db_error_fails_closed(self):
        """Any exception talking to Postgres → fails closed (not present)."""
        with patch.object(watchdog, "psycopg") as pg_mock:
            pg_mock.connect.side_effect = RuntimeError("connection refused")
            present, why = watchdog._check_r4_arm_present("dummy-dsn")
        assert present is False
        assert "boot_briefing read failed" in why
        assert "connection refused" in why
