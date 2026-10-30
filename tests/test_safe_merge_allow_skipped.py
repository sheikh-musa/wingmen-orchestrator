"""Regression tests for scripts/safe_merge.sh's --allow-skipped FLAG PARSING.

tests/test_merge_gate.py covers merge_gate.evaluate_checks()'s Python decision
logic directly. Nothing covered safe_merge.sh's OWN bash flag parsing: the
repeatable --allow-skipped build into the ALLOW=() array, and the pass-through
of every accumulated pair to merge_gate.py's CLI. That gap matters on the Mac
Mini's /bin/bash 3.2, where an empty array expansion under `set -u` is quirky
and the `${ALLOW[@]+"${ALLOW[@]}"}` guard is load-bearing.

These tests run the REAL script as a subprocess (explicitly /bin/bash) with a
stubbed `gh` on PATH (tests/fixtures/fake_gh/gh), so they need no network and
no real PR. Every case is --dry-run, so no merge is ever attempted.
"""
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SAFE_MERGE = REPO_ROOT / "scripts" / "safe_merge.sh"
FAKE_GH_DIR = REPO_ROOT / "tests" / "fixtures" / "fake_gh"
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"


def _checks(*pairs):
    return [{"name": name, "state": state} for name, state in pairs]


def _run_safe_merge(tmp_path, checks, allow_skipped=()):
    """Run safe_merge.sh --dry-run with a stubbed gh; return CompletedProcess."""
    view = {"state": "OPEN", "mergeable": "MERGEABLE", "headRefOid": HEAD_SHA}
    env = os.environ.copy()
    env["PATH"] = str(FAKE_GH_DIR) + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_VIEW_JSON"] = json.dumps(view)
    env["FAKE_GH_HEAD_SHA"] = HEAD_SHA
    env["FAKE_GH_CHECKS_JSON"] = json.dumps(checks)
    cmd = ["/bin/bash", str(SAFE_MERGE), "--repo", "owner/repo", "1", "--dry-run"]
    for name in allow_skipped:
        cmd += ["--allow-skipped", name]
    return subprocess.run(
        cmd, env=env, capture_output=True, text=True, timeout=30, cwd=tmp_path
    )


def test_zero_allow_flags_all_success_accepts(tmp_path):
    # Proves the EMPTY ALLOW array expands cleanly under `set -u` on bash 3.2:
    # without the `${ALLOW[@]+"${ALLOW[@]}"}` guard this path dies with
    # "unbound variable" and safe_merge could never merge a PR whose checks are
    # all SUCCESS (i.e. the common case).
    result = _run_safe_merge(
        tmp_path,
        _checks(("Lint & Test", "SUCCESS"), ("build", "SUCCESS")),
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "DRY-RUN" in result.stdout


def test_skipped_check_without_allow_flag_refuses(tmp_path):
    result = _run_safe_merge(
        tmp_path,
        _checks(("Lint & Test", "SUCCESS"), ("e2e-tests", "SKIPPED")),
    )
    assert result.returncode == 3, (result.stdout, result.stderr)
    assert "REFUSE" in result.stderr


def test_one_allow_skipped_accepts(tmp_path):
    result = _run_safe_merge(
        tmp_path,
        _checks(("Lint & Test", "SUCCESS"), ("e2e-tests", "SKIPPED")),
        allow_skipped=["e2e-tests"],
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "DRY-RUN" in result.stdout


def test_two_repeated_allow_skipped_flags_both_pass_through(tmp_path):
    # The actual gap: prove ALLOW=() accumulates more than one value and
    # forwards every pair, not just the first or the last.
    result = _run_safe_merge(
        tmp_path,
        _checks(
            ("Lint & Test", "SUCCESS"),
            ("e2e-tests", "SKIPPED"),
            ("docs", "SKIPPED"),
        ),
        allow_skipped=["e2e-tests", "docs"],
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "DRY-RUN" in result.stdout


def test_two_flags_still_refuse_an_unlisted_skipped_check(tmp_path):
    # Multi-flag mode must not become "allow everything": a third SKIPPED
    # check that was never named still fails closed.
    result = _run_safe_merge(
        tmp_path,
        _checks(
            ("Lint & Test", "SUCCESS"),
            ("e2e-tests", "SKIPPED"),
            ("docs", "SKIPPED"),
            ("security", "SKIPPED"),
        ),
        allow_skipped=["e2e-tests", "docs"],
    )
    assert result.returncode == 3, (result.stdout, result.stderr)
    # merge_gate.py prints the per-check reason on stdout; the script's own
    # stderr line is generic.
    assert "security" in result.stdout
