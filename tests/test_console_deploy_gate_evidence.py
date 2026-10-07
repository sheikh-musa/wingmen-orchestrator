"""scripts/console_deploy_gate_evidence.py — G1 unit-tests evidence (orch-console
bus #57372): a 92-passed/0-failed console deploy run was scored G1 "fail" in
shadow mode because the old check scanned the WHOLE pytest.log for the bare
substrings "failed"/"error" anywhere, which false-triggers on unrelated text
containing those words (nervous_system/protected_agents.py's own fallback
UserWarning literally says "DB read failed (OperationalError(...))" on every
run without a live local Postgres socket). Caught in shadow mode, before G1
was ever enforced — these tests pin the fix: blew_up is scoped to pytest's own
final summary line, not the whole log."""
from __future__ import annotations

import importlib
from pathlib import Path

cdge = importlib.import_module("scripts.console_deploy_gate_evidence")


def _deploy_dir(tmp_path, pytest_log_text: str | None = None) -> Path:
    d = tmp_path / "deploy"
    d.mkdir()
    if pytest_log_text is not None:
        (d / "pytest.log").write_text(pytest_log_text)
    return d


# ── the real false-positive this fixes (bus #57372) ──────────────────────────

def test_clean_pass_with_unrelated_failed_and_error_text_scores_pass(tmp_path):
    """The exact af3dbc38 shape: a fully-passing run whose warnings section
    contains the substrings 'failed' and 'error' in unrelated text."""
    log = (
        "=============================== warnings summary ===============================\n"
        "nervous_system/protected_agents.py:194\n"
        "  .../protected_agents.py:194: UserWarning: protected_agents: DB read failed "
        "(OperationalError('connection is bad: ... failed to connect')) — falling back "
        "to the static floor\n"
        "    rows = _safe_registry_rows(dsn)\n"
        "\n"
        "-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n"
        "92 passed, 3 warnings in 95.88s (0:01:35)\n"
    )
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, log))
    assert evidence["checks"]["unit-tests"] == "pass"


def test_clean_pass_with_error_word_in_a_docstring_or_log_line_scores_pass(tmp_path):
    log = (
        "collected 10 items\n"
        "tests/test_error_handling.py ..........\n"  # filename itself contains 'error'
        "10 passed in 1.23s\n"
    )
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, log))
    assert evidence["checks"]["unit-tests"] == "pass"


# ── real failures must still be caught ────────────────────────────────────────

def test_real_test_failures_still_score_fail(tmp_path):
    log = (
        "FAILED tests/console/test_app.py::test_something - AssertionError\n"
        "3 failed, 89 passed, 2 warnings in 12.34s\n"
    )
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, log))
    assert evidence["checks"]["unit-tests"] == "fail"


def test_collection_errors_with_zero_passed_score_fail(tmp_path):
    log = "ERROR tests/test_broken.py - ImportError: cannot import name 'x'\n5 errors in 1.23s\n"
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, log))
    assert evidence["checks"]["unit-tests"] == "fail"


def test_mixed_passed_and_failed_in_same_summary_line_scores_fail(tmp_path):
    log = "1 failed, 1 error, 90 passed, 1 warning in 5.0s\n"
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, log))
    assert evidence["checks"]["unit-tests"] == "fail"


# ── the #45683 regression this file already protects against (unchanged) ────

def test_empty_log_scores_fail_not_pass(tmp_path):
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, ""))
    assert evidence["checks"]["unit-tests"] == "fail"


def test_truncated_log_with_no_summary_line_scores_fail(tmp_path):
    log = "collecting... \ntests/test_x.py "  # cut off mid-run, no summary ever printed
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, log))
    assert evidence["checks"]["unit-tests"] == "fail"


# ── no pytest.log at all: check stays absent, not fabricated ─────────────────

def test_missing_pytest_log_leaves_unit_tests_check_absent(tmp_path):
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, None))
    assert "unit-tests" not in evidence["checks"]


# ── multiple summary-shaped lines: the LAST one (pytest's real final summary) wins ─

def test_takes_the_last_summary_line_when_the_log_has_more_than_one(tmp_path):
    """A re-run or a nested subprocess could print an earlier 'N passed' line
    before the real final one -- the last line in the file is pytest's own
    terminal summary and must be the one scored."""
    log = (
        "3 failed, 1 passed in 1.0s\n"  # some earlier, unrelated summary-shaped line
        "...\n"
        "92 passed, 3 warnings in 95.88s (0:01:35)\n"  # the real, final summary
    )
    evidence = cdge.build_evidence("deadbeef", _deploy_dir(tmp_path, log))
    assert evidence["checks"]["unit-tests"] == "pass"
