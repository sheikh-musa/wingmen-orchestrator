"""Tests for scripts/lib/merge_gate.evaluate_checks (orch-console #48411).

The gate must REFUSE anything not provably green — especially a PENDING/in_progress
check (the 2026-10-01 PR#243 hole where a merge raced the build).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import merge_gate  # noqa: E402


def _c(name, state):
    return {"name": name, "state": state}


def test_all_success_passes():
    ok, _ = merge_gate.evaluate_checks([_c("Lint & Test", "SUCCESS"), _c("build", "SUCCESS")])
    assert ok is True


def test_pending_refuses():  # the #243 hole
    ok, reason = merge_gate.evaluate_checks([_c("Lint & Test", "PENDING")])
    assert ok is False and "Lint & Test" in reason


def test_in_progress_refuses():
    ok, _ = merge_gate.evaluate_checks([_c("Lint & Test", "IN_PROGRESS")])
    assert ok is False


def test_failure_refuses():
    ok, _ = merge_gate.evaluate_checks([_c("Lint & Test", "SUCCESS"), _c("e2e", "FAILURE")])
    assert ok is False


def test_neutral_never_waived():
    ok, _ = merge_gate.evaluate_checks([_c("x", "NEUTRAL")])
    assert ok is False


def test_zero_checks_refuses():
    ok, reason = merge_gate.evaluate_checks([])
    assert ok is False and "zero checks" in reason


def test_skipped_refuses_unless_allowed():
    checks = [_c("Lint & Test", "SUCCESS"), _c("e2e-tests", "SKIPPED")]
    assert merge_gate.evaluate_checks(checks)[0] is False
    assert merge_gate.evaluate_checks(checks, allow_skipped={"e2e-tests"})[0] is True
