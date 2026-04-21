"""Tests for scripts/lib/check_family_preflight.py — TASK-045.

The helper queries agents + agent_status for DB-level preconditions that
the bash preflight can't easily express. Tests mock the psycopg.connect
call so we don't need a live DSN.
"""
from unittest.mock import MagicMock, patch

import pytest

from scripts.lib.check_family_preflight import (
    check_family_preflight,
    ExitCode,
)


def _mock_cursor_with_results(results: list):
    """Return a psycopg-like context-manager chain that yields `results` from fetchone/fetchall.

    Convention:
    - fetchone() consumes elements from `results` in order (one per agents query).
    - fetchall() returns the last element of `results` as the siblings row-list
      (only reached when the agents row check passes).
    """
    # fetchall.side_effect is a one-element list so the first call returns results[-1]
    # directly (the siblings list).  For tests that exit before fetchall is called
    # this value is never consumed, so the sentinel None is fine.
    fetchall_payload = results[-1] if len(results) > 1 else None

    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone = MagicMock(side_effect=results)
    cur.fetchall = MagicMock(side_effect=[fetchall_payload])

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor = MagicMock(return_value=cur)

    return conn


def test_missing_agents_row_returns_exit_3():
    """agents row absent → ExitCode.AGENTS_MISSING (3)."""
    conn = _mock_cursor_with_results([None])  # first fetchone (agents row) → None

    with patch("scripts.lib.check_family_preflight.psycopg.connect", return_value=conn):
        code, msg = check_family_preflight(
            family_id="cc-scholar",
            dsn="postgresql://fake",
        )
    assert code == ExitCode.AGENTS_MISSING
    assert "cc-scholar" in msg


def test_empty_repo_scope_returns_exit_3():
    """agents row present but repo_scope empty → ExitCode.AGENTS_MISSING (3)."""
    conn = _mock_cursor_with_results([("cc-scholar", [], None)])  # (id, repo_scope, last_heartbeat)

    with patch("scripts.lib.check_family_preflight.psycopg.connect", return_value=conn):
        code, msg = check_family_preflight(
            family_id="cc-scholar",
            dsn="postgresql://fake",
        )
    assert code == ExitCode.AGENTS_MISSING
    assert "repo_scope" in msg.lower() or "empty" in msg.lower()


def test_active_sibling_returns_exit_7():
    """Fresh agent_status row for sibling → ExitCode.ACTIVE_SIBLING (7)."""
    from datetime import datetime, timezone, timedelta
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    conn = _mock_cursor_with_results([
        ("cc-scholar", ["ai-scholar", "hifz-companion"], None),  # agents row: scope ok, dark
        [("cc-scholar_1", recent)],  # active siblings query (fetchall)
    ])

    with patch("scripts.lib.check_family_preflight.psycopg.connect", return_value=conn):
        code, msg = check_family_preflight(
            family_id="cc-scholar",
            dsn="postgresql://fake",
        )
    assert code == ExitCode.ACTIVE_SIBLING
    assert "cc-scholar_1" in msg


def test_all_clear_returns_exit_0():
    """Agents row good, no active siblings → ExitCode.OK (0)."""
    conn = _mock_cursor_with_results([
        ("cc-scholar", ["ai-scholar", "hifz-companion"], None),
        [],  # no active siblings
    ])

    with patch("scripts.lib.check_family_preflight.psycopg.connect", return_value=conn):
        code, msg = check_family_preflight(
            family_id="cc-scholar",
            dsn="postgresql://fake",
        )
    assert code == ExitCode.OK
    assert "ok" in msg.lower() or "ready" in msg.lower()
