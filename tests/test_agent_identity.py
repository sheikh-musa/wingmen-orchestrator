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
