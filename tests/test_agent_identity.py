"""scripts/lib/agent_identity.py — the shared fail-closed "who am I" resolver
(bus #47221, extracted from bus_send.py's resolve_from_agent so asks_triage.py
/ asks_open.py can reuse the same logic instead of re-deriving it). Full
behavioral coverage already lives in test_bus_send.py's identity tests, which
exercise this module indirectly via bus_send.resolve_from_agent (a direct
re-export) — this file just pins the canonical import path."""
import importlib
import json
import subprocess

import pytest

ai = importlib.import_module("scripts.lib.agent_identity")


def test_resolve_agent_id_prefers_cc_base_agent_id():
    env = {"CC_BASE_AGENT_ID": "cc-oeh", "AGENT_ID": "should-not-win"}
    assert ai.resolve_agent_id(env) == "cc-oeh"


def test_resolve_agent_id_refuses_rather_than_guesses():
    with pytest.raises(ai.IdentityError):
        ai.resolve_agent_id({})


def test_resolve_agent_id_requires_console_boot_marker_not_just_role(monkeypatch):
    # bus #85112/#85131: ORCH_BODY_ROLE=console/ORCH_AGENT_ID=orch-console are
    # static .env defaults any process inherits by sourcing it -- must not
    # resolve without boot_nazim.sh's own ORCH_CONSOLE_BOOT=1 marker.
    env = {"ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console"}
    with pytest.raises(ai.IdentityError):
        ai.resolve_agent_id(env)
    env["ORCH_CONSOLE_BOOT"] = "1"
    assert ai.resolve_agent_id(env) == "orch-console"


def test_resolve_agent_id_outside_tmux_with_env_sourced_refuses():
    # console's own ask (bus #85145): an ordinary ops shell that merely
    # sources the shared .env (ORCH_BODY_ROLE=console/ORCH_AGENT_ID=
    # orch-console present, no TMUX at all) must refuse -- this is the exact
    # live leak (#85124), reproduced with no tmux involved at all so the
    # subprocess path in _in_console_tmux_session is never even reached.
    env = {"ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console"}
    assert "TMUX" not in env
    with pytest.raises(ai.IdentityError):
        ai.resolve_agent_id(env)


def test_resolve_agent_id_in_nazim_tmux_with_no_flag_resolves_orch_console(monkeypatch):
    # console's own ask (bus #85145): the CURRENT live console session
    # predates ORCH_CONSOLE_BOOT and will never have it set without a
    # restart -- it must still resolve correctly via the actual tmux
    # session name alone.
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="nazim\n", stderr="")

    monkeypatch.setattr(ai.subprocess, "run", _fake_run)
    env = {
        "ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console",
        "TMUX": "/tmp/tmux-501/default,1234,0", "ORCH_TMUX_SESSION": "nazim",
    }
    assert ai.resolve_agent_id(env) == "orch-console"
    assert calls and calls[0][:3] == ["tmux", "display-message", "-p"]


def test_in_console_tmux_session_false_when_session_name_differs(monkeypatch):
    monkeypatch.setattr(
        ai.subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="some-lane\n", stderr=""),
    )
    env = {"TMUX": "/tmp/tmux-501/default,1234,0", "ORCH_TMUX_SESSION": "nazim"}
    assert ai._in_console_tmux_session(env) is False


def test_in_console_tmux_session_false_when_not_in_tmux_at_all():
    assert ai._in_console_tmux_session({"ORCH_TMUX_SESSION": "nazim"}) is False


def test_in_console_tmux_session_false_on_tmux_error(monkeypatch):
    monkeypatch.setattr(
        ai.subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no session"),
    )
    env = {"TMUX": "/tmp/tmux-501/default,1234,0", "ORCH_TMUX_SESSION": "nazim"}
    assert ai._in_console_tmux_session(env) is False


def test_bus_send_reexports_the_same_function():
    bs = importlib.import_module("scripts.bus_send")
    assert bs.resolve_from_agent is ai.resolve_agent_id
    assert bs.IdentityError is ai.IdentityError


# ── resolve_base_agent_id_for_hook (bus #68342) ──────────────────────────────
# A PreToolUse/PostToolUse hook subprocess doesn't reliably inherit
# CC_BASE_AGENT_ID from the launching claude process — this is the fallback
# that resolves it from the per-worktree marker launch_dangerous_cc.sh writes,
# keyed by `cwd` (what a hook's own JSON payload reliably carries).

def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _write_marker(tmp_path, base_agent_id="cc-irsyad-coord", agent_id="cc-irsyad-coord"):
    r = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--absolute-git-dir"],
        capture_output=True, text=True, check=True,
    )
    git_dir = r.stdout.strip()
    with open(f"{git_dir}/cc-lane-identity.json", "w", encoding="utf-8") as fh:
        json.dump({"base_agent_id": base_agent_id, "agent_id": agent_id}, fh)


def test_resolve_for_hook_prefers_env_when_present(tmp_path):
    _init_repo(tmp_path)
    _write_marker(tmp_path, base_agent_id="cc-from-marker")
    env = {"CC_BASE_AGENT_ID": "cc-from-env"}
    assert ai.resolve_base_agent_id_for_hook(str(tmp_path), env=env) == "cc-from-env"


def test_resolve_for_hook_falls_back_to_marker_when_env_absent(tmp_path):
    _init_repo(tmp_path)
    _write_marker(tmp_path, base_agent_id="cc-irsyad-coord")
    assert ai.resolve_base_agent_id_for_hook(str(tmp_path), env={}) == "cc-irsyad-coord"


def test_resolve_for_hook_empty_when_no_env_and_no_marker(tmp_path):
    _init_repo(tmp_path)
    assert ai.resolve_base_agent_id_for_hook(str(tmp_path), env={}) == ""


def test_resolve_for_hook_empty_when_cwd_not_a_repo(tmp_path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    assert ai.resolve_base_agent_id_for_hook(str(not_a_repo), env={}) == ""


def test_resolve_for_hook_empty_when_cwd_is_none():
    assert ai.resolve_base_agent_id_for_hook(None, env={}) == ""


def test_read_lane_identity_marker_empty_on_malformed_json(tmp_path):
    _init_repo(tmp_path)
    r = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--absolute-git-dir"],
        capture_output=True, text=True, check=True,
    )
    with open(f"{r.stdout.strip()}/cc-lane-identity.json", "w", encoding="utf-8") as fh:
        fh.write("not json")
    assert ai.read_lane_identity_marker(str(tmp_path)) == {}
