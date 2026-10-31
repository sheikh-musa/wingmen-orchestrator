"""Tests for scripts/loop/run_pilot.py's launch_role() env scrub (PR#661
residual LOW, cc-quality bus #83795): the role subprocess must never inherit
the orchestrator checkout's absolute path via ORCH_DIR / ORCH_ROOT -- both are
on the shared lane allow-list's SAFE tier
(scripts/lib/lane_env_allowlist.sh), so before this scrub they reached the
role's `claude` process. The isolation invariant
(docs/loop-pilot-design-v1.md) says a role only ever sees its own worktree.
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
    returncode = 0
    stdout = ""
    stderr = ""


def test_launch_role_scrubs_orch_dir_and_orch_root(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_DIR", str(tmp_path / "orch"))
    monkeypatch.setenv("ORCH_ROOT", str(tmp_path / "orch"))
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return _FakeCompleted()

    monkeypatch.setattr(run_pilot.subprocess, "run", fake_run)

    run_pilot.launch_role("builder", worktree=tmp_path / "worktree",
                          model="deepseek-flash", max_turns=5, dry_run=False)

    env = captured["env"]
    assert env is not None
    assert "ORCH_DIR" not in env
    assert "ORCH_ROOT" not in env
    # Control: the rest of the role env is still built as before.
    assert env["MODEL"] == "deepseek-flash"
    assert env["CC_BASE_OVERRIDE"] == "cc-loop"
