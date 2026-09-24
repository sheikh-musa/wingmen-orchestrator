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


# ── the pure function: ensure_settings(settings, kv) ─────────────────────────────
# Enforces top-level scalar settings (Nazim #43083 dialog/suggestion-wedge bundle):
# promptSuggestionEnabled=False, feedbackDrafts="off". Unlike deny (list-append), a
# scalar setting is ENFORCED to the desired value — an autonomous lane must not carry
# a stale/human-left value that re-opens the wedge. Preserves every other key.
def test_ensure_settings_sets_on_empty():
    s = {}
    changed = eld.ensure_settings(s, {"promptSuggestionEnabled": False, "feedbackDrafts": "off"})
    assert changed is True
    assert s == {"promptSuggestionEnabled": False, "feedbackDrafts": "off"}


def test_ensure_settings_idempotent_when_already_equal():
    s = {"promptSuggestionEnabled": False, "feedbackDrafts": "off"}
    changed = eld.ensure_settings(s, {"promptSuggestionEnabled": False, "feedbackDrafts": "off"})
    assert changed is False
    assert s == {"promptSuggestionEnabled": False, "feedbackDrafts": "off"}


def test_ensure_settings_overwrites_a_differing_value():
    # a lane carrying feedbackDrafts="on" (wedge-exposed) is corrected to "off"
    s = {"feedbackDrafts": "on"}
    changed = eld.ensure_settings(s, {"feedbackDrafts": "off"})
    assert changed is True
    assert s["feedbackDrafts"] == "off"


def test_ensure_settings_preserves_other_keys():
    s = {"permissions": {"deny": ["AskUserQuestion"]}, "model": "opus"}
    changed = eld.ensure_settings(s, {"promptSuggestionEnabled": False})
    assert changed is True
    assert s["permissions"]["deny"] == ["AskUserQuestion"]
    assert s["model"] == "opus"
    assert s["promptSuggestionEnabled"] is False


def test_ensure_settings_preserves_bool_false_vs_absent():
    # False is a real enforced value, not "absent" — must not be treated as unset
    s = {"promptSuggestionEnabled": False}
    changed = eld.ensure_settings(s, {"promptSuggestionEnabled": False})
    assert changed is False


# ── ensure_user_global(): the USER-scope feedbackDrafts enforcer ───────────────────
# feedbackDrafts is "User or managed" scope (code.claude.com settings-reference) — it
# is IGNORED in project/local settings, so it must be enforced in ~/.claude/settings.json,
# NOT in the lane's settings.local.json. These tests always target a tmp path so they
# never touch the real user-global file.
def _read(path):
    with open(path) as f:
        return json.load(f)


def test_ensure_user_global_sets_feedbackdrafts(tmp_path):
    ug = tmp_path / "user.json"
    changed = eld.ensure_user_global({"feedbackDrafts": "off"}, str(ug))
    assert changed is True
    assert _read(ug)["feedbackDrafts"] == "off"


def test_ensure_user_global_idempotent(tmp_path):
    ug = tmp_path / "user.json"
    ug.write_text(json.dumps({"feedbackDrafts": "off", "model": "opus"}))
    changed = eld.ensure_user_global({"feedbackDrafts": "off"}, str(ug))
    assert changed is False
    assert _read(ug)["model"] == "opus"  # untouched


def test_ensure_user_global_corrects_drift_preserving_other_keys(tmp_path):
    ug = tmp_path / "user.json"
    ug.write_text(json.dumps({"feedbackDrafts": "notify", "permissions": {"deny": ["X"]}}))
    changed = eld.ensure_user_global({"feedbackDrafts": "off"}, str(ug))
    assert changed is True
    out = _read(ug)
    assert out["feedbackDrafts"] == "off"
    assert out["permissions"]["deny"] == ["X"]  # nothing else clobbered


def test_ensure_user_global_malformed_left_untouched_returns_false(tmp_path):
    ug = tmp_path / "user.json"
    ug.write_text("{bad json")
    changed = eld.ensure_user_global({"feedbackDrafts": "off"}, str(ug))
    assert changed is False                 # never blocks / never overwrites unparseable
    assert ug.read_text() == "{bad json"


# ── main(): file I/O paths ───────────────────────────────────────────────────────
def test_main_writes_wedge_prevention_settings(tmp_path):
    # LOCAL gets promptSuggestionEnabled (Any-file scope); feedbackDrafts does NOT go
    # to local (it is User-scope-only and would be silently ignored there).
    ug = tmp_path / "user.json"
    rc = eld.main(["--cwd", str(tmp_path), "--user-settings", str(ug)])
    assert rc == 0
    out = _read(tmp_path / ".claude" / "settings.local.json")
    assert out["permissions"]["deny"] == ["AskUserQuestion"]
    assert out["promptSuggestionEnabled"] is False
    assert "feedbackDrafts" not in out       # user-scope-only, never written to local
    # user-global gets feedbackDrafts
    assert _read(ug)["feedbackDrafts"] == "off"


def test_main_empty_dir_creates_file(tmp_path):
    ug = tmp_path / "user.json"
    rc = eld.main(["--cwd", str(tmp_path), "--user-settings", str(ug)])
    assert rc == 0
    out = tmp_path / ".claude" / "settings.local.json"
    assert out.exists()
    assert _read(out) == {
        "permissions": {"deny": ["AskUserQuestion"]},
        "promptSuggestionEnabled": False,
    }


def test_main_merges_without_clobbering(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.local.json").write_text(json.dumps(
        {"hooks": {"PreToolUse": [{"matcher": "Bash"}]}, "permissions": {"allow": ["Read"]}}))
    rc = eld.main(["--cwd", str(tmp_path), "--user-settings", str(tmp_path / "user.json")])
    assert rc == 0
    out = _read(claude / "settings.local.json")
    assert out["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert out["permissions"]["allow"] == ["Read"]
    assert out["permissions"]["deny"] == ["AskUserQuestion"]


def test_main_idempotent_second_run(tmp_path):
    ug = str(tmp_path / "user.json")
    eld.main(["--cwd", str(tmp_path), "--user-settings", ug])
    before = (tmp_path / ".claude" / "settings.local.json").read_text()
    eld.main(["--cwd", str(tmp_path), "--user-settings", ug])
    after = (tmp_path / ".claude" / "settings.local.json").read_text()
    assert before == after


def test_main_malformed_file_left_untouched_and_returns_zero(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    bad = claude / "settings.local.json"
    bad.write_text("{bad json")
    rc = eld.main(["--cwd", str(tmp_path), "--user-settings", str(tmp_path / "user.json")])
    assert rc == 0                      # never blocks the launch
    assert bad.read_text() == "{bad json"  # refused to touch it


def test_main_missing_dir_returns_zero(tmp_path):
    rc = eld.main(["--cwd", str(tmp_path / "does-not-exist"),
                   "--user-settings", str(tmp_path / "user.json")])
    assert rc == 0
