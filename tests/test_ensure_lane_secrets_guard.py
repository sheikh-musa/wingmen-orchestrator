"""Unit tests for scripts/lib/ensure_lane_secrets_guard.py — the launcher's
PreToolUse secrets_transcript_guard enforcer (bus #53680/#53678). Locks in the
by-hand cases so a regression that drops the hook from the shared launcher fails
CI, not a lane that never gets proactive coverage on a new host."""
import importlib.util
import json
import pathlib

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "ensure_lane_secrets_guard.py"
_spec = importlib.util.spec_from_file_location("ensure_lane_secrets_guard", _MOD_PATH)
elsg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(elsg)

_CMD = "/fake/venv/bin/python3 /fake/scripts/hooks/secrets_transcript_guard.py"


def _read(path):
    with open(path) as f:
        return json.load(f)


# ── the pure function: ensure_secrets_guard(settings, command) ───────────────────
def test_empty_settings_creates_hook_group():
    s = {}
    changed = elsg.ensure_secrets_guard(s, _CMD)
    assert changed is True
    assert s["hooks"]["PreToolUse"][0]["matcher"] == elsg.HOOK_MATCHER
    assert s["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == _CMD
    assert s["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == elsg.HOOK_TIMEOUT


def test_existing_unrelated_hook_and_permissions_preserved():
    s = {
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "other_guard.py"}]}]},
        "permissions": {"deny": ["AskUserQuestion"]},
    }
    changed = elsg.ensure_secrets_guard(s, _CMD)
    assert changed is True
    # nothing clobbered
    assert s["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "other_guard.py"
    assert s["permissions"]["deny"] == ["AskUserQuestion"]
    # new group appended, not merged into the existing one
    assert len(s["hooks"]["PreToolUse"]) == 2
    assert s["hooks"]["PreToolUse"][1]["hooks"][0]["command"] == _CMD


def test_idempotent_no_change_when_already_present():
    s = {}
    elsg.ensure_secrets_guard(s, _CMD)
    before = json.dumps(s, sort_keys=True)
    changed = elsg.ensure_secrets_guard(s, _CMD)
    assert changed is False
    assert json.dumps(s, sort_keys=True) == before


def test_idempotent_even_if_matcher_widens_later():
    # matches by command string alone — a future matcher change never duplicates the entry
    s = {}
    elsg.ensure_secrets_guard(s, _CMD, matcher="Bash")
    changed = elsg.ensure_secrets_guard(s, _CMD, matcher="Bash|Read|Agent")
    assert changed is False
    assert len(s["hooks"]["PreToolUse"]) == 1


def test_non_dict_hooks_is_normalised():
    s = {"hooks": "oops"}
    changed = elsg.ensure_secrets_guard(s, _CMD)
    assert changed is True
    assert s["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == _CMD


def test_non_list_pretooluse_is_normalised():
    s = {"hooks": {"PreToolUse": "oops"}}
    changed = elsg.ensure_secrets_guard(s, _CMD)
    assert changed is True
    assert s["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == _CMD


def test_group_with_non_dict_entry_is_skipped_not_crashed():
    s = {"hooks": {"PreToolUse": ["not-a-dict"]}}
    changed = elsg.ensure_secrets_guard(s, _CMD)
    assert changed is True
    assert len(s["hooks"]["PreToolUse"]) == 2


# ── main(): file I/O paths ────────────────────────────────────────────────────────
def test_main_missing_dir_returns_zero(tmp_path):
    rc = elsg.main(["--cwd", str(tmp_path / "does-not-exist")])
    assert rc == 0


def test_main_malformed_file_left_untouched_and_returns_zero(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    bad = claude / "settings.local.json"
    bad.write_text("{bad json")
    rc = elsg.main(["--cwd", str(tmp_path)])
    assert rc == 0
    assert bad.read_text() == "{bad json"


def test_main_skips_when_venv_or_guard_script_missing_on_this_host(tmp_path, monkeypatch):
    # simulates a host where the orchestrator live checkout's venv/hook script
    # don't exist at the resolved path — must skip silently, never write a command
    # that would fail on every tool call.
    monkeypatch.setattr(elsg, "_VENV_PY", str(tmp_path / "nonexistent-venv" / "python3"))
    rc = elsg.main(["--cwd", str(tmp_path)])
    assert rc == 0
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def _patch_existing_paths(monkeypatch, tmp_path):
    """main() only writes when both resolved paths exist on disk (the real
    module-level constants depend on this CHECKOUT having a .venv, which a git
    worktree — including the one these tests often run from — never carries).
    Point the module at guaranteed-existing files instead, so the happy path is
    tested deterministically regardless of ambient checkout state."""
    fake_venv = tmp_path / "fake-venv-python3"
    fake_venv.write_text("")
    fake_guard = tmp_path / "fake-guard-script.py"
    fake_guard.write_text("")
    monkeypatch.setattr(elsg, "_VENV_PY", str(fake_venv))
    monkeypatch.setattr(elsg, "_GUARD_SCRIPT", str(fake_guard))
    return f"{fake_venv} {fake_guard}"


def test_main_writes_hook_when_resolved_paths_exist(tmp_path, monkeypatch):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    expected_cmd = _patch_existing_paths(monkeypatch, tmp_path)
    rc = elsg.main(["--cwd", str(lane_dir)])
    assert rc == 0
    out = _read(lane_dir / ".claude" / "settings.local.json")
    assert out["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == expected_cmd


def test_main_idempotent_second_run(tmp_path, monkeypatch):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    _patch_existing_paths(monkeypatch, tmp_path)
    elsg.main(["--cwd", str(lane_dir)])
    before = (lane_dir / ".claude" / "settings.local.json").read_text()
    elsg.main(["--cwd", str(lane_dir)])
    after = (lane_dir / ".claude" / "settings.local.json").read_text()
    assert before == after


def test_main_merges_without_clobbering_existing_local_settings(tmp_path, monkeypatch):
    lane_dir = tmp_path / "lane"
    claude = lane_dir / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.local.json").write_text(json.dumps(
        {"permissions": {"deny": ["AskUserQuestion"]}, "promptSuggestionEnabled": False}))
    expected_cmd = _patch_existing_paths(monkeypatch, tmp_path)
    rc = elsg.main(["--cwd", str(lane_dir)])
    assert rc == 0
    out = _read(claude / "settings.local.json")
    assert out["permissions"]["deny"] == ["AskUserQuestion"]
    assert out["promptSuggestionEnabled"] is False
    assert out["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == expected_cmd
