"""Unit tests for scripts/lib/ensure_lane_deny.py — the launcher's AskUserQuestion
deny enforcer (Nazim #42983/#42990). Locks in the by-hand cases so a regression that
lets a lane open the human-blocking menu fails CI, not a live lane."""
import importlib.util
import json
import os
import pathlib

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "ensure_lane_deny.py"
_spec = importlib.util.spec_from_file_location("ensure_lane_deny", _MOD_PATH)
eld = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eld)


# ── the pure function: ensure_deny(settings, deny_tools) ─────────────────────────
def test_empty_settings_creates_deny():
    s = {}
    changed = eld.ensure_deny(s, ["AskUserQuestion"])
    assert changed is True
    assert s == {"permissions": {"deny": ["AskUserQuestion"]}}


def test_existing_hooks_and_permissions_preserved_deny_appended():
    s = {
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard.sh"}]}]},
        "permissions": {"allow": ["Bash(ls:*)"], "deny": ["WebFetch"]},
    }
    changed = eld.ensure_deny(s, ["AskUserQuestion"])
    assert changed is True
    # nothing clobbered
    assert s["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert s["permissions"]["allow"] == ["Bash(ls:*)"]
    # deny appended, WebFetch kept, order stable
    assert s["permissions"]["deny"] == ["WebFetch", "AskUserQuestion"]


def test_idempotent_no_change_when_already_present():
    s = {"permissions": {"deny": ["AskUserQuestion"]}}
    changed = eld.ensure_deny(s, ["AskUserQuestion"])
    assert changed is False
    assert s == {"permissions": {"deny": ["AskUserQuestion"]}}


def test_non_list_deny_is_normalised():
    # a malformed-but-parseable deny (wrong type) is replaced with a proper list
    s = {"permissions": {"deny": "AskUserQuestion"}}
    changed = eld.ensure_deny(s, ["AskUserQuestion"])
    assert changed is True
    assert s["permissions"]["deny"] == ["AskUserQuestion"]


def test_non_dict_permissions_is_normalised():
    s = {"permissions": "oops"}
    changed = eld.ensure_deny(s, ["AskUserQuestion"])
    assert changed is True
    assert s["permissions"]["deny"] == ["AskUserQuestion"]


def test_multiple_deny_tools():
    s = {}
    eld.ensure_deny(s, ["AskUserQuestion", "SomethingElse"])
    assert s["permissions"]["deny"] == ["AskUserQuestion", "SomethingElse"]


# ── main(): file I/O paths ───────────────────────────────────────────────────────
def _read(path):
    with open(path) as f:
        return json.load(f)


def test_main_empty_dir_creates_file(tmp_path):
    rc = eld.main(["--cwd", str(tmp_path)])
    assert rc == 0
    out = tmp_path / ".claude" / "settings.local.json"
    assert out.exists()
    assert _read(out) == {"permissions": {"deny": ["AskUserQuestion"]}}


def test_main_merges_without_clobbering(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.local.json").write_text(json.dumps(
        {"hooks": {"PreToolUse": [{"matcher": "Bash"}]}, "permissions": {"allow": ["Read"]}}))
    rc = eld.main(["--cwd", str(tmp_path)])
    assert rc == 0
    out = _read(claude / "settings.local.json")
    assert out["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert out["permissions"]["allow"] == ["Read"]
    assert out["permissions"]["deny"] == ["AskUserQuestion"]


def test_main_idempotent_second_run(tmp_path):
    eld.main(["--cwd", str(tmp_path)])
    before = (tmp_path / ".claude" / "settings.local.json").read_text()
    eld.main(["--cwd", str(tmp_path)])
    after = (tmp_path / ".claude" / "settings.local.json").read_text()
    assert before == after


def test_main_malformed_file_left_untouched_and_returns_zero(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    bad = claude / "settings.local.json"
    bad.write_text("{bad json")
    rc = eld.main(["--cwd", str(tmp_path)])
    assert rc == 0                      # never blocks the launch
    assert bad.read_text() == "{bad json"  # refused to touch it


def test_main_missing_dir_returns_zero(tmp_path):
    rc = eld.main(["--cwd", str(tmp_path / "does-not-exist")])
    assert rc == 0
