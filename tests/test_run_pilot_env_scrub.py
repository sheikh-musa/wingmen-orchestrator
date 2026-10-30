"""Tests for scripts/loop/run_pilot.py's launch_role() env scrub (PR#661
residual LOW, orch-console bus #83795): a role session is launched in an
isolated hifz-companion worktree and must never be able to locate the
orchestrator checkout's hidden task-data tree (tasks/loop-pilot/<id>/hidden/).
launch_role() builds its subprocess env as dict(os.environ), and ORCH_DIR /
ORCH_ROOT (on the fleet-wide lane allow-list's SAFE tier) would otherwise ride
through to the role's process. These tests assert the env dict actually passed
to subprocess.run carries neither key.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "scripts" / "loop" / "run_pilot.py"
spec = importlib.util.spec_from_file_location("run_pilot", MODULE_PATH)
run_pilot = importlib.util.module_from_spec(spec)
sys.modules["run_pilot"] = run_pilot
spec.loader.exec_module(run_pilot)


class _FakeCompleted:
    def __init__(self):
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""


def test_launch_role_scrubs_orch_dir_and_orch_root_from_env(monkeypatch, tmp_path):
    # ORCH_DIR / ORCH_ROOT present in the inherited environment, as they are on
    # a real lane boot.
    monkeypatch.setenv("ORCH_DIR", "/Users/sheikhmusa/wingmen/orchestrator")
    monkeypatch.setenv("ORCH_ROOT", "/Users/sheikhmusa/wingmen/orchestrator")

    captured = {}

    def fake_run(cmd, cwd=None, env=None, capture_output=False, text=False):
        captured["env"] = env
        return _FakeCompleted()

    monkeypatch.setattr(run_pilot.subprocess, "run", fake_run)

    # A non-claude model keeps launch_role off the OAuth-token-resolution and
    # cost-extraction paths -- this test only exercises the env scrub.
    rr = run_pilot.launch_role(
        role="builder", worktree=tmp_path, model="deepseek-flash",
        max_turns=10, dry_run=False)

    assert rr.returncode == 0
    env = captured["env"]
    assert env is not None, "subprocess.run was not called with an env dict"
    assert "ORCH_DIR" not in env
    assert "ORCH_ROOT" not in env
    # The scrub is surgical: the env it does set still goes through.
    assert env.get("MODEL") == "deepseek-flash"
    assert env.get("CC_BASE_OVERRIDE") == "cc-loop"
