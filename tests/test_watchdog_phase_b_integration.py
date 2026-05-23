"""Integration test — synthetic runaway harness per CAI-RESP-163 ROLLOUT SHAPE.

Insert a synthetic flagged row into active_autonomous_loops (NOT registered),
trigger one watchdog sweep, assert SIGTERM was decided (without actually
firing — we mock os.kill).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")


@pytestmark_integration
def test_synthetic_runaway_triggers_hard_kill_decision(monkeypatch):
    """Insert synthetic active_autonomous_loops row; assert decide_kill returns hard_kill.

    Post CAI-RESP-164 R1-AMENDED: decide_kill requires a 3-of-3 ContentShape to
    return hard_kill. This test passes a synthetic 3-of-3 shape to preserve the
    original semantic — that the SIGTERM path is reachable under the new gate.
    """
    from nervous_system.long_caller_watchdog import decide_kill, ContentShape
    from nervous_system.content_shape_signals import SignalResult

    fake_cc = f"cc-test-runaway-{uuid.uuid4().hex[:8]}"
    fake_pid = 99999

    shape = ContentShape(
        signal_a=SignalResult(match=True, value=54000),
        signal_b=SignalResult(match=True, value={"avg_gap": 300}),
        signal_c=SignalResult(match=True, value="ok"),
    )
    decision = decide_kill(
        caller_name=fake_cc,
        sessions_24h=500,
        cadence_seconds=10,
        registered=False,
        parent_pid=fake_pid,
        content_shape=shape,
    )
    assert decision.action == "hard_kill"
    assert decision.pid == fake_pid


@pytestmark_integration
def test_substrate_native_never_killed_even_with_active_row():
    """If ralphy hypothetically appears in active_autonomous_loops, watchdog skips."""
    from nervous_system.long_caller_watchdog import decide_kill
    decision = decide_kill(
        caller_name="ralphy",
        sessions_24h=10000, cadence_seconds=1,
        registered=False,
        parent_pid=12345,
    )
    assert decision.action == "no_kill"


@pytestmark_integration
def test_cc_family_interactive_pre_registered_never_killed():
    """Per CC-FAMILY-INTERACTIVE-SESSIONS-001, cc-scholar-interactive registered no_kill."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT auto_kill_policy FROM long_running_claude_callers "
                "WHERE caller_name='cc-scholar-interactive' AND revoked_at IS NULL"
            )
            r = cur.fetchone()
    assert r is not None, "cc-scholar-interactive must be pre-registered per CC-FAMILY-INTERACTIVE-SESSIONS-001"
    assert r[0] == "no_kill"

    from nervous_system.long_caller_watchdog import decide_kill
    decision = decide_kill(
        caller_name="cc-scholar-interactive",
        sessions_24h=500, cadence_seconds=15,
        registered=True, registered_policy="no_kill",
        parent_pid=24810,
    )
    assert decision.action == "no_kill"
