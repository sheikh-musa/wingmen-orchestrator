"""Tests for scripts/arm_glm_image_read_guard.sh — the POST-MERGE user-level arming
for the GLM image-Read residency guard (PR#397 / #65950).

The load-bearing safety claim: the pre-check REFUSES to arm (changing nothing) unless
the hook no-ops (exit 0) on a Claude-lane env — otherwise user-level registration
would fail-closed-block EVERY Bash/Read on EVERY Claude lane fleet-wide. These tests
drive the real hook + real venv python through the script against a TEMP settings
file (never ~/.claude/settings.json).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
ARM = REPO / "scripts" / "arm_glm_image_read_guard.sh"
HOOK = REPO / "scripts" / "hooks" / "glm_image_read_guard.py"
PY = sys.executable  # the orchestrator venv python running these tests


def _run(settings, hook=HOOK, rollback=False):
    env = dict(os.environ)
    env.update({"GLM_GUARD_HOOK": str(hook), "GLM_GUARD_PY": PY, "GLM_GUARD_SETTINGS": str(settings)})
    args = ["bash", str(ARM)] + (["--rollback"] if rollback else [])
    return subprocess.run(args, text=True, capture_output=True, env=env)


def _pre(settings):
    return (json.load(open(settings)).get("hooks") or {}).get("PreToolUse") or []


def _guard_entries(pre):
    return [e for e in pre if "glm_image_read_guard" in json.dumps(e)]


def test_arm_registers_user_level_with_absolute_paths_and_preserves_existing(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "echo hi", "timeout": 5}]}]}}))
    r = _run(s)
    assert r.returncode == 0, r.stderr
    pre = _pre(s)
    ge = _guard_entries(pre)
    assert len(ge) == 1
    cmd = ge[0]["hooks"][0]["command"]
    assert str(HOOK) in cmd and PY in cmd and cmd.count('"') == 4  # both abs paths quoted
    # pre-existing unrelated hook survives
    assert any(e.get("matcher") == "Write" for e in pre)


def test_arm_is_idempotent(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("{}")
    assert _run(s).returncode == 0
    assert _run(s).returncode == 0
    assert len(_guard_entries(_pre(s))) == 1


def test_rollback_removes_guard_and_keeps_others(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "echo hi", "timeout": 5}]}]}}))
    _run(s)
    assert len(_guard_entries(_pre(s))) == 1
    r = _run(s, rollback=True)
    assert r.returncode == 0, r.stderr
    assert len(_guard_entries(_pre(s))) == 0
    assert any(e.get("matcher") == "Write" for e in _pre(s))  # unrelated kept


def test_precheck_aborts_when_hook_blocks_claude_env(tmp_path):
    """A hook that exits non-zero on a Claude env MUST abort arming, leaving settings
    untouched — the catastrophic fail-closed-fleet-wide mode must never be armed."""
    bad = tmp_path / "bad_hook.py"
    bad.write_text("import sys\nsys.exit(1)\n")
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {"PreToolUse": []}}))
    r = _run(s, hook=bad)
    assert r.returncode != 0
    assert "PRE-CHECK FAILED" in r.stderr
    assert _pre(s) == []  # unchanged


def test_precheck_aborts_when_hook_missing(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("{}")
    r = _run(s, hook=tmp_path / "does_not_exist.py")
    assert r.returncode != 0
    assert "ARM-ABORT" in r.stderr
