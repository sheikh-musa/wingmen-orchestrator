"""Pure-unit tests for long_caller_watchdog.py per CAI-RESP-163.

Six mandatory cases:
  (a) Synthetic runaway harness — registered=False + cadence pattern → hard_kill
  (b) PID-recycle race — pid still alive but cwd mismatch → abort
  (c) Just-registered race — caller registered between detector + kill → abort
  (d) Substrate-native carve-out — ralphy/paused-job-retry → never_kill
  (e) Panic button — WINGMEN_LONG_CALLER_WATCHDOG_DISABLED → no SIGTERM
  (f) Telegram body-shape — Q7 mandated inline-action-menu format
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.long_caller_watchdog import (
    KillDecision,
    decide_kill,
    build_telegram_body,
    SUBSTRATE_NATIVE_NEVER_KILL,
    CadenceTracker,
)


class TestSubstrateNativeCarveOut:
    """C2 frozenset belt-and-suspenders — check FIRST before any other rule."""

    def test_ralphy_never_killed_even_with_pattern(self):
        decision = decide_kill(
            caller_name="ralphy",
            sessions_24h=10_000,
            cadence_seconds=1,
            registered=False,
            parent_pid=12345,
        )
        assert decision.action == "no_kill"
        assert decision.reason.startswith("substrate_native")

    def test_paused_job_retry_never_killed(self):
        decision = decide_kill(
            caller_name="paused-job-retry",
            sessions_24h=10_000, cadence_seconds=1,
            registered=False, parent_pid=12345,
        )
        assert decision.action == "no_kill"

    def test_frozenset_immutable(self):
        with pytest.raises((AttributeError, TypeError)):
            SUBSTRATE_NATIVE_NEVER_KILL.add("evil")  # type: ignore


class TestPanicButton:
    """Q-final WINGMEN_LONG_CALLER_WATCHDOG_DISABLED env flag."""

    def test_panic_button_set_skips_kill(self, monkeypatch):
        monkeypatch.setenv("WINGMEN_LONG_CALLER_WATCHDOG_DISABLED", "true")
        decision = decide_kill(
            caller_name="cc-evil-runaway",
            sessions_24h=10_000, cadence_seconds=1,
            registered=False, parent_pid=12345,
        )
        assert decision.action == "no_kill"
        assert "panic_button" in decision.reason

    def test_panic_button_unset_normal_kill_flow(self, monkeypatch):
        monkeypatch.delenv("WINGMEN_LONG_CALLER_WATCHDOG_DISABLED", raising=False)
        # Updated for CAI-RESP-164 R1-AMENDED: pass 3-of-3 ContentShape so the
        # SIGTERM path is reachable (verifies panic-unset doesn't short-circuit).
        from nervous_system.long_caller_watchdog import ContentShape
        from nervous_system.content_shape_signals import SignalResult
        shape = ContentShape(
            signal_a=SignalResult(match=True, value=54000),
            signal_b=SignalResult(match=True, value={"avg_gap": 300}),
            signal_c=SignalResult(match=True, value="ok"),
        )
        decision = decide_kill(
            caller_name="cc-evil-runaway",
            sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=shape,
        )
        assert decision.action == "hard_kill"


class TestR1HardKill:
    """R1 — unregistered + cadence pattern → hard_kill (synthetic runaway shape)."""

    def test_unregistered_pattern_kills(self):
        # Updated for CAI-RESP-164 R1-AMENDED: hard_kill now requires 3-of-3
        # content-shape signal match in addition to the unregistered + cadence
        # pattern. Pass a 3-of-3 ContentShape so the SIGTERM path still lights up.
        from nervous_system.long_caller_watchdog import ContentShape
        from nervous_system.content_shape_signals import SignalResult
        shape = ContentShape(
            signal_a=SignalResult(match=True, value=54000),
            signal_b=SignalResult(match=True, value={"avg_gap": 300}),
            signal_c=SignalResult(match=True, value="ok"),
        )
        decision = decide_kill(
            caller_name="cc-rogue",
            sessions_24h=200,
            cadence_seconds=300,
            registered=False,
            parent_pid=12345,
            content_shape=shape,
        )
        assert decision.action == "hard_kill"
        assert decision.pid == 12345

    def test_registered_with_pattern_does_not_hard_kill(self):
        """Pre-registered CC families (CC-FAMILY-INTERACTIVE-SESSIONS-001) are exempt."""
        decision = decide_kill(
            caller_name="cc-scholar-interactive",
            sessions_24h=400, cadence_seconds=15,
            registered=True, registered_policy="no_kill",
            parent_pid=12345,
        )
        assert decision.action == "no_kill"


class TestPidRecycleGuard:
    """Q3 amendment: re-verify cwd matches at kill time."""

    def test_pid_recycled_to_different_cwd_aborts(self, monkeypatch):
        """Mock the cwd-check to return a non-cc-* dir (simulating PID reuse)."""
        from nervous_system import long_caller_watchdog as mod
        from nervous_system.long_caller_watchdog import ContentShape
        from nervous_system.content_shape_signals import SignalResult

        # Updated for CAI-RESP-164 R1-AMENDED: inner decide_kill must reach
        # hard_kill before the PID-recycle guard fires. Provide 3-of-3 shape.
        shape = ContentShape(
            signal_a=SignalResult(match=True, value=54000),
            signal_b=SignalResult(match=True, value={"avg_gap": 300}),
            signal_c=SignalResult(match=True, value="ok"),
        )
        monkeypatch.setattr(mod, "_resolve_pid_cwd", lambda pid: "/usr/bin")
        decision = mod.decide_kill_with_pid_verify(
            caller_name="cc-rogue",
            sessions_24h=200, cadence_seconds=300,
            registered=False,
            parent_pid=12345,
            expected_cwd_prefix="/Users/sheikhmusa/wingmen/projects/",
            content_shape=shape,
        )
        assert decision.action == "no_kill"
        assert "pid_recycled" in decision.reason


class TestJustRegisteredRace:
    """C1: caller registers between detector sweep N and N+1 → abort kill."""

    def test_just_registered_aborts_kill(self):
        decision = decide_kill(
            caller_name="cc-newly-registered",
            sessions_24h=200, cadence_seconds=300,
            registered=True,
            registered_policy="soft_alert",
            parent_pid=12345,
        )
        assert decision.action != "hard_kill"


class TestTelegramBodyShape:
    """Q7 amendment: inline action menu with /legitimize and /revoke."""

    def test_kill_alert_body_includes_action_menu(self):
        body = build_telegram_body(
            event="hard_kill",
            caller_name="cc-rogue",
            pid=3410,
            cwd="/Users/cc-rogue",
            sessions_24h=285,
            threshold=50,
        )
        assert "🛑" in body
        assert "Watchdog killed unregistered caller" in body
        assert "cc-rogue" in body
        assert "PID 3410" in body
        assert "285" in body
        assert "/legitimize" in body
        assert "/revoke" in body

    def test_soft_alert_body_distinct_from_kill(self):
        body = build_telegram_body(
            event="soft_alert_cadence_drift",
            caller_name="cc-x",
            pid=1234,
            cwd="/Users/cc-x",
            sessions_24h=500,
            threshold=50,
        )
        assert "🛑" not in body
        assert "drift" in body.lower() or "soft" in body.lower()


class TestCadenceTracker:
    """In-memory 30min sliding window for R3 drift detection."""

    def test_first_observation_no_drift(self):
        t = CadenceTracker(expected_cadence_seconds=300)
        result = t.observe(caller_name="cc-x", observed_at=1000.0)
        assert result.drift_detected is False

    def test_drift_2x_over_window_triggers(self):
        t = CadenceTracker(expected_cadence_seconds=300)
        for i in range(40):
            t.observe(caller_name="cc-x", observed_at=1000.0 + i * 60)
        result = t.observe(caller_name="cc-x", observed_at=1000.0 + 40 * 60)
        assert result.drift_detected is True

    def test_drift_resets_after_recovery(self):
        t = CadenceTracker(expected_cadence_seconds=300)
        for i in range(10):
            t.observe(caller_name="cc-x", observed_at=1000.0 + i * 300)
        result = t.observe(caller_name="cc-x", observed_at=1000.0 + 10 * 300)
        assert result.drift_detected is False
