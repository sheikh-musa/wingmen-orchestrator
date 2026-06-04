"""Tests for cc_cai_daemon.kill_switch — INV-6 default HOLD enforcement.

Per CAI-RESP-185 Q5 rail (b): the kill-switch reverts cc-cai-daemon to
pure-escalation mode on confidence drop. Three states:
  live                  — normal operation (silent-lane + escalation)
  pure_escalation_mode  — every message escalates regardless of classification
  panic_disabled        — env flag set; no actions at all (operator override)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cc_cai_daemon.kill_switch import (
    KillSwitch,
    STATE_LIVE,
    STATE_PURE_ESCALATION,
    STATE_PANIC_DISABLED,
    CONFIDENCE_DROP_THRESHOLD,
    CONFIDENCE_DROP_CONSECUTIVE,
    PANIC_ENV_VAR,
)


class TestInitialState:
    def test_starts_live_with_no_env_flag(self, monkeypatch):
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        ks = KillSwitch(audit=MagicMock())
        assert ks.state == STATE_LIVE

    def test_starts_panic_when_env_flag_set(self, monkeypatch):
        monkeypatch.setenv(PANIC_ENV_VAR, "true")
        ks = KillSwitch(audit=MagicMock())
        assert ks.state == STATE_PANIC_DISABLED

    def test_env_flag_case_insensitive_truthy(self, monkeypatch):
        for val in ("true", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv(PANIC_ENV_VAR, val)
            ks = KillSwitch(audit=MagicMock())
            assert ks.state == STATE_PANIC_DISABLED, f"value {val!r} should panic"

    def test_env_flag_falsy_stays_live(self, monkeypatch):
        for val in ("false", "0", "no", ""):
            monkeypatch.setenv(PANIC_ENV_VAR, val)
            ks = KillSwitch(audit=MagicMock())
            assert ks.state == STATE_LIVE, f"value {val!r} should stay live"


class TestConfidenceDropTrip:
    def test_single_low_confidence_doesnt_trip(self, monkeypatch):
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        ks = KillSwitch(audit=MagicMock())
        ks.observe(confidence=0.3)
        assert ks.state == STATE_LIVE

    def test_three_consecutive_low_confidence_trips_to_pure_escalation(self, monkeypatch):
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        audit = MagicMock()
        ks = KillSwitch(audit=audit)
        ks.observe(confidence=0.3)
        ks.observe(confidence=0.4)
        ks.observe(confidence=0.2)
        assert ks.state == STATE_PURE_ESCALATION
        audit.log_kill_switch_trip.assert_called_once()
        kwargs = audit.log_kill_switch_trip.call_args.kwargs
        assert kwargs["new_state"] == STATE_PURE_ESCALATION
        assert "confidence_drop" in kwargs["reason"]

    def test_high_confidence_resets_consecutive_counter(self, monkeypatch):
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        ks = KillSwitch(audit=MagicMock())
        ks.observe(confidence=0.3)
        ks.observe(confidence=0.4)
        ks.observe(confidence=0.9)  # reset
        ks.observe(confidence=0.3)
        ks.observe(confidence=0.4)
        # Two consecutive after reset — not yet 3 — still live
        assert ks.state == STATE_LIVE

    def test_at_threshold_is_not_low(self, monkeypatch):
        """confidence == CONFIDENCE_DROP_THRESHOLD is the boundary — must NOT count as low."""
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        ks = KillSwitch(audit=MagicMock())
        for _ in range(5):
            ks.observe(confidence=CONFIDENCE_DROP_THRESHOLD)
        assert ks.state == STATE_LIVE

    def test_subsequent_observations_after_trip_no_double_log(self, monkeypatch):
        """Once tripped, further low-confidence observations don't keep logging trips."""
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        audit = MagicMock()
        ks = KillSwitch(audit=audit)
        for _ in range(CONFIDENCE_DROP_CONSECUTIVE):
            ks.observe(confidence=0.1)
        # Tripped — single log
        assert audit.log_kill_switch_trip.call_count == 1
        # Further low-conf observations — no additional logs
        for _ in range(5):
            ks.observe(confidence=0.1)
        assert audit.log_kill_switch_trip.call_count == 1


class TestActionGate:
    def test_live_allows_silent_action(self, monkeypatch):
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        ks = KillSwitch(audit=MagicMock())
        assert ks.should_act_silently() is True
        assert ks.should_escalate() is True

    def test_pure_escalation_mode_blocks_silent_action(self, monkeypatch):
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        ks = KillSwitch(audit=MagicMock())
        ks._state = STATE_PURE_ESCALATION
        assert ks.should_act_silently() is False
        assert ks.should_escalate() is True

    def test_panic_disabled_blocks_both(self, monkeypatch):
        monkeypatch.setenv(PANIC_ENV_VAR, "true")
        ks = KillSwitch(audit=MagicMock())
        assert ks.should_act_silently() is False
        assert ks.should_escalate() is False


class TestManualTrip:
    def test_force_pure_escalation(self, monkeypatch):
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        audit = MagicMock()
        ks = KillSwitch(audit=audit)
        ks.force_state(STATE_PURE_ESCALATION, reason="operator_request")
        assert ks.state == STATE_PURE_ESCALATION
        audit.log_kill_switch_trip.assert_called_once_with(
            new_state=STATE_PURE_ESCALATION, reason="operator_request",
        )

    def test_force_invalid_state_raises(self, monkeypatch):
        monkeypatch.delenv(PANIC_ENV_VAR, raising=False)
        ks = KillSwitch(audit=MagicMock())
        with pytest.raises(ValueError):
            ks.force_state("invalid_state", reason="oops")
