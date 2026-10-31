"""The installer that puts the powers guard into every lane, from the launcher.

Same shape and safety contract as ensure_lane_secrets_guard (bus #53680/#53678):
per-worktree, untracked settings.local.json, merge-safe (only ever ADDS a hook
group), idempotent, atomic, fail-loud-but-exit-0 so a settings hiccup can never
take a lane down.

TG2.0 section 3 (orch-console #86250).
"""
import importlib.util
import json
from pathlib import Path

LIB = Path(__file__).parent.parent / "scripts" / "lib"
INSTALLER = LIB / "ensure_client_powers_guard.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ecpg():
    return _load("ensure_client_powers_guard", INSTALLER)


def test_installs_a_bash_hook_group():
    m = ecpg()
    settings = {}
    command = "/x/.venv/bin/python3 /x/scripts/hooks/client_powers_guard.py"
    assert m.ensure_client_powers_guard(settings, command) is True
    groups = settings["hooks"]["PreToolUse"]
    assert len(groups) == 1
    assert groups[0]["matcher"] == "Bash"
    assert groups[0]["hooks"][0]["command"] == command
    assert groups[0]["hooks"][0]["timeout"] == 10


def test_is_idempotent():
    m = ecpg()
    settings = {}
    command = "/x/client_powers_guard.py"
    assert m.ensure_client_powers_guard(settings, command) is True
    assert m.ensure_client_powers_guard(settings, command) is False
    assert len(settings["hooks"]["PreToolUse"]) == 1


def test_preserves_every_other_group_and_other_settings():
    m = ecpg()
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "other_guard.py"}]}
    settings = {"hooks": {"PreToolUse": [other], "PostToolUse": [{"matcher": "Bash"}]},
                "permissions": {"deny": ["AskUserQuestion"]}}
    assert m.ensure_client_powers_guard(settings, "/x/client_powers_guard.py") is True
    assert settings["hooks"]["PreToolUse"][0] == other
    assert len(settings["hooks"]["PreToolUse"]) == 2
    assert settings["hooks"]["PostToolUse"] == [{"matcher": "Bash"}]
    assert settings["permissions"] == {"deny": ["AskUserQuestion"]}


def test_repairs_a_malformed_pretooluse_shape():
    m = ecpg()
    for bad in ("oops", ["not-a-dict"], None):
        settings = {"hooks": {"PreToolUse": bad}}
        assert m.ensure_client_powers_guard(settings, "/x/client_powers_guard.py") is True
        assert isinstance(settings["hooks"]["PreToolUse"], list)
        assert settings["hooks"]["PreToolUse"][-1]["hooks"][0]["command"] == "/x/client_powers_guard.py"


def test_main_wires_the_powers_guard_command_into_the_worktree(tmp_path, monkeypatch):
    m = ecpg()
    # Point the module's own resolved paths at fake-but-existing files so the
    # "missing on this host" branch does not swallow the write.
    venv_py = tmp_path / "python3"
    guard = tmp_path / "client_powers_guard.py"
    venv_py.write_text("#!/bin/sh\n")
    guard.write_text("# hook\n")
    monkeypatch.setattr(m, "_VENV_PY", str(venv_py))
    monkeypatch.setattr(m, "_GUARD_SCRIPT", str(guard))
    lane = tmp_path / "lane"
    lane.mkdir()

    assert m.main(["--cwd", str(lane)]) == 0
    settings = json.loads((lane / ".claude" / "settings.local.json").read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == f"{venv_py} {guard}"
    assert guard.name in command


def test_main_is_a_no_op_when_the_guard_is_not_on_this_host(tmp_path, monkeypatch):
    m = ecpg()
    monkeypatch.setattr(m, "_VENV_PY", str(tmp_path / "absent-python3"))
    monkeypatch.setattr(m, "_GUARD_SCRIPT", str(tmp_path / "absent_guard.py"))
    lane = tmp_path / "lane"
    lane.mkdir()
    assert m.main(["--cwd", str(lane)]) == 0
    assert not (lane / ".claude" / "settings.local.json").exists()
