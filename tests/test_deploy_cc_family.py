"""Tests for scripts/deploy_cc_family.sh — TASK-045.

Pattern mirrors tests/test_check_lock_keys.py: invoke the script via
subprocess with fixtures under tmp_path, assert on exit code + stderr.

Fixtures override the repo-root and projects-root via env vars so the
script can be tested in isolation without touching ~/wingmen/projects.
"""
import os
import stat
import subprocess
from pathlib import Path
from typing import Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = _REPO_ROOT / "scripts" / "deploy_cc_family.sh"


def _run(args: list[str], env_overrides: Optional[dict] = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_script_is_executable():
    """CI and operators need the script to run without explicit `bash` prefix."""
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/deploy_cc_family.sh must be +x"


def test_no_args_shows_usage():
    """No family-id → usage message + non-zero exit."""
    r = _run([])
    assert r.returncode != 0
    assert "usage" in r.stderr.lower() or "usage" in r.stdout.lower()
    assert "family-id" in (r.stderr + r.stdout).lower()


def test_unknown_family_id_exits_2(tmp_path):
    """Unknown family-id → exit 2 with clear error."""
    r = _run(["cc-bogus"])
    assert r.returncode == 2, f"expected 2, got {r.returncode}. stderr:{r.stderr}"
    assert "unknown family" in r.stderr.lower() or "cc-bogus" in r.stderr


def test_known_family_id_passes_family_check(tmp_path):
    """Known family-id → passes past the family-ID gate. Downstream checks
    (repo clone etc.) will fail, but NOT with exit 2."""
    # Point projects root at empty tmp_path so repo-clone check fails with exit 4,
    # which proves we got past the family-id validator (exit 2).
    r = _run(
        ["cc-scholar"],
        env_overrides={"CC_PROJECTS_ROOT": str(tmp_path)},
    )
    assert r.returncode != 2, f"family-id check rejected cc-scholar. stderr:{r.stderr}"


def test_missing_repo_clone_exits_4(tmp_path):
    """Repo missing from CC_PROJECTS_ROOT → exit 4 with the repo name."""
    r = _run(
        ["cc-scholar"],
        env_overrides={"CC_PROJECTS_ROOT": str(tmp_path)},
    )
    assert r.returncode == 4, f"expected 4, got {r.returncode}. stderr:{r.stderr}"
    # cc-scholar's repo_scope is ['ai-scholar','hifz-companion'] — error should mention at least one
    assert "ai-scholar" in r.stderr or "hifz-companion" in r.stderr


def test_present_repo_clone_passes_clone_check(tmp_path):
    """All repos present in CC_PROJECTS_ROOT → passes clone check.
    Downstream checks (.env etc.) still fail, but NOT with exit 4."""
    (tmp_path / "ai-scholar" / ".git").mkdir(parents=True)
    (tmp_path / "hifz-companion" / ".git").mkdir(parents=True)
    r = _run(
        ["cc-scholar"],
        env_overrides={"CC_PROJECTS_ROOT": str(tmp_path)},
    )
    assert r.returncode != 4, f"clone check rejected despite repos present. stderr:{r.stderr}"
