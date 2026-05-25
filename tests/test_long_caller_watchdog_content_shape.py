"""Tests for content_shape integration into decide_kill (CAI-RESP-164 R1-AMENDED)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.long_caller_watchdog import decide_kill, ContentShape
from nervous_system.content_shape_signals import SignalResult


def _shape(a, b, c, unobs=False) -> ContentShape:
    return ContentShape(
        signal_a=SignalResult(match=a, value=None, unobservable=(a is None and unobs)),
        signal_b=SignalResult(match=b, value=None, unobservable=(b is None and unobs)),
        signal_c=SignalResult(match=c, value=None, unobservable=(c is None and unobs)),
    )


class TestContentShapeGate:
    def test_3_of_3_match_unregistered_hard_kill(self):
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, True),
        )
        assert d.action == "hard_kill"

    def test_2_of_3_match_unregistered_monitored(self):
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, False),
        )
        assert d.action == "monitored"

    def test_0_of_3_match_unregistered_monitored(self):
        d = decide_kill(
            caller_name="cc-scholar-active-but-unregistered",
            sessions_24h=400, cadence_seconds=20,
            registered=False, parent_pid=12345,
            content_shape=_shape(False, False, False),
        )
        assert d.action == "monitored"

    def test_any_unobservable_no_action(self):
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, None, unobs=True),
        )
        assert d.action == "no_action"

    def test_substrate_native_still_wins_over_3_of_3(self):
        d = decide_kill(
            caller_name="ralphy", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, True),
        )
        assert d.action == "no_kill"

    def test_panic_button_still_wins(self, monkeypatch):
        monkeypatch.setenv("WINGMEN_LONG_CALLER_WATCHDOG_DISABLED", "1")
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, True),
        )
        assert d.action == "no_kill"

    def test_registered_no_kill_policy_wins_over_3_of_3(self):
        d = decide_kill(
            caller_name="cc-scholar-interactive",
            sessions_24h=400, cadence_seconds=20,
            registered=True, registered_policy="no_kill",
            parent_pid=12345,
            content_shape=_shape(True, True, True),
        )
        assert d.action == "no_kill"

    def test_omitting_content_shape_no_hard_kill(self):
        """Backward compat — calling decide_kill without content_shape no longer hard_kills."""
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
        )
        assert d.action != "hard_kill"
