"""Unit tests for scripts/lib/ensure_user_global_hooks.py — closes the "a new
orchestrator PreToolUse guard never reaches an off-tree body" gap (orch-console
bus #86124, 2026-10-31: live_checkout_edit_guard landed in orchestrator's own
project-level .claude/settings.json but was never copied into cc-fleet-health's
hand-maintained user-global one, so it fired for nobody off-tree). Locks in the
resolve/diff/merge logic so a regression here silently reopens that class of gap.
"""
import importlib.util
import json
import pathlib

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "ensure_user_global_hooks.py"
_spec = importlib.util.spec_from_file_location("ensure_user_global_hooks", _MOD_PATH)
euh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(euh)


def _read(path):
    with open(path) as f:
        return json.load(f)


# ── resolve_project_dir_var ──────────────────────────────────────────────────

def test_resolve_project_dir_var_quoted_form():
    cmd = '"$CLAUDE_PROJECT_DIR"/.venv/bin/python3 "$CLAUDE_PROJECT_DIR"/scripts/hooks/x.py'
    resolved = euh.resolve_project_dir_var(cmd)
    assert "$CLAUDE_PROJECT_DIR" not in resolved
    assert resolved == f'"{euh._ORCH_DIR}"/.venv/bin/python3 "{euh._ORCH_DIR}"/scripts/hooks/x.py'


def test_resolve_project_dir_var_unquoted_form():
    cmd = "$CLAUDE_PROJECT_DIR/.venv/bin/python3 $CLAUDE_PROJECT_DIR/scripts/hooks/x.py"
    resolved = euh.resolve_project_dir_var(cmd)
    assert resolved == f"{euh._ORCH_DIR}/.venv/bin/python3 {euh._ORCH_DIR}/scripts/hooks/x.py"


def test_resolve_project_dir_var_noop_when_absent():
    cmd = "/abs/venv/bin/python3 /abs/scripts/hooks/x.py"
    assert euh.resolve_project_dir_var(cmd) == cmd


# ── project_pretooluse_commands ──────────────────────────────────────────────

def test_project_pretooluse_commands_extracts_matcher_command_timeout():
    project = {
        "hooks": {"PreToolUse": [
            {"matcher": "Edit|Write", "hooks": [
                {"type": "command", "command": "$CLAUDE_PROJECT_DIR/hooks/a.py", "timeout": 10},
            ]},
        ]},
    }
    out = euh.project_pretooluse_commands(project)
    assert out == [("Edit|Write", f"{euh._ORCH_DIR}/hooks/a.py", 10)]


def test_project_pretooluse_commands_skips_non_command_types_and_blank():
    project = {
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "something-else", "command": "ignored.py"},
                {"type": "command", "command": "   "},
                {"type": "command", "command": "real.py"},
            ]},
        ]},
    }
    out = euh.project_pretooluse_commands(project)
    assert [c for _, c, _ in out] == ["real.py"]


def test_project_pretooluse_commands_empty_when_no_hooks_key():
    assert euh.project_pretooluse_commands({}) == []


# ── existing_pretooluse_commands / missing_commands ──────────────────────────

def test_missing_commands_finds_only_absent_ones():
    project = {
        "hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "/abs/a.py", "timeout": 10}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/abs/b.py", "timeout": 10}]},
        ]},
    }
    user = {
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/abs/b.py"}]},
        ]},
    }
    missing = euh.missing_commands(project, user)
    assert [c for _, c, _ in missing] == ["/abs/a.py"]


def test_missing_commands_all_when_user_settings_empty():
    project = {
        "hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "/abs/a.py", "timeout": 10}]},
        ]},
    }
    assert [c for _, c, _ in euh.missing_commands(project, {})] == ["/abs/a.py"]


# ── quoting-insensitive comparison (bus #86124 follow-up) ───────────────────

def test_missing_commands_not_fooled_by_quoting_style():
    # project settings' own style: "$CLAUDE_PROJECT_DIR"/x -- resolves to "/abs"/x
    project = {
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command",
             "command": '"$CLAUDE_PROJECT_DIR"/.venv/bin/python3 "$CLAUDE_PROJECT_DIR"/hooks/x.py',
             "timeout": 10}]},
        ]},
    }
    # user-global, hand-typed, unquoted
    user_unquoted = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command",
         "command": f"{euh._ORCH_DIR}/.venv/bin/python3 {euh._ORCH_DIR}/hooks/x.py"}]},
    ]}}
    assert euh.missing_commands(project, user_unquoted) == []
    # user-global, hand-typed, fully quoted
    user_fully_quoted = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command",
         "command": f'"{euh._ORCH_DIR}/.venv/bin/python3" "{euh._ORCH_DIR}/hooks/x.py"'}]},
    ]}}
    assert euh.missing_commands(project, user_fully_quoted) == []


def test_missing_commands_real_quoting_still_finds_genuinely_absent():
    project = {
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command",
             "command": '"$CLAUDE_PROJECT_DIR"/hooks/present.py', "timeout": 10}]},
            {"matcher": "Bash", "hooks": [{"type": "command",
             "command": '"$CLAUDE_PROJECT_DIR"/hooks/absent.py', "timeout": 10}]},
        ]},
    }
    user = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command",
         "command": f'"{euh._ORCH_DIR}/hooks/present.py"'}]},
    ]}}
    missing = euh.missing_commands(project, user)
    assert len(missing) == 1
    # returned command keeps the project file's OWN quoting style (resolved,
    # not re-normalized) -- that's what would be written on --apply, and it's
    # functionally identical to the shell either way.
    assert missing[0][1] == f'"{euh._ORCH_DIR}"/hooks/absent.py'
    assert euh._normalize_command(missing[0][1]) == (f"{euh._ORCH_DIR}/hooks/absent.py",)


# ── merge_missing: preserves everything else, appends groups ────────────────

def test_merge_missing_preserves_unrelated_keys_and_existing_hooks():
    user = {
        "permissions": {"defaultMode": "bypassPermissions"},
        "model": "claude-sonnet-5",
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/abs/existing.py", "timeout": 10}]},
        ]},
    }
    changed = euh.merge_missing(user, [("Edit|Write", "/abs/new.py", 10)])
    assert changed is True
    assert user["permissions"]["defaultMode"] == "bypassPermissions"
    assert user["model"] == "claude-sonnet-5"
    assert len(user["hooks"]["PreToolUse"]) == 2
    assert user["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "/abs/existing.py"
    assert user["hooks"]["PreToolUse"][1] == {
        "matcher": "Edit|Write", "hooks": [{"type": "command", "command": "/abs/new.py", "timeout": 10}],
    }


def test_merge_missing_noop_when_nothing_missing():
    user = {"hooks": {"PreToolUse": []}}
    before = json.dumps(user, sort_keys=True)
    changed = euh.merge_missing(user, [])
    assert changed is False
    assert json.dumps(user, sort_keys=True) == before


# ── main(): --check (default, report-only) vs --apply ───────────────────────

def test_main_check_mode_writes_nothing_and_exits_1_when_missing(tmp_path, capsys):
    project_path = tmp_path / "project-settings.json"
    project_path.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command",
             "command": "$CLAUDE_PROJECT_DIR/hooks/a.py", "timeout": 10}]},
        ]},
    }))
    user_path = tmp_path / "user-settings.json"
    user_path.write_text(json.dumps({"model": "claude-sonnet-5"}))

    rc = euh.main(["--project-settings", str(project_path), "--user-settings", str(user_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "MISSING" in out
    # untouched — report-only
    assert _read(user_path) == {"model": "claude-sonnet-5"}


def test_main_apply_mode_writes_merge_and_exits_0(tmp_path, capsys):
    project_path = tmp_path / "project-settings.json"
    project_path.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command",
             "command": "$CLAUDE_PROJECT_DIR/hooks/a.py", "timeout": 10}]},
        ]},
    }))
    user_path = tmp_path / "user-settings.json"
    user_path.write_text(json.dumps({"model": "claude-sonnet-5"}))

    rc = euh.main(["--apply", "--project-settings", str(project_path), "--user-settings", str(user_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "added 1 missing hook" in out
    written = _read(user_path)
    assert written["model"] == "claude-sonnet-5"  # preserved
    assert written["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == f"{euh._ORCH_DIR}/hooks/a.py"


def test_main_apply_is_idempotent(tmp_path):
    project_path = tmp_path / "project-settings.json"
    project_path.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command",
             "command": "$CLAUDE_PROJECT_DIR/hooks/a.py", "timeout": 10}]},
        ]},
    }))
    user_path = tmp_path / "user-settings.json"
    user_path.write_text(json.dumps({}))

    euh.main(["--apply", "--project-settings", str(project_path), "--user-settings", str(user_path)])
    after_first = _read(user_path)
    rc = euh.main(["--apply", "--project-settings", str(project_path), "--user-settings", str(user_path)])
    assert rc == 0
    assert _read(user_path) == after_first  # no duplicate group added


def test_main_up_to_date_exits_0_when_nothing_missing(tmp_path, capsys):
    project_path = tmp_path / "project-settings.json"
    project_path.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "/abs/a.py", "timeout": 10}]},
        ]},
    }))
    user_path = tmp_path / "user-settings.json"
    user_path.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "/abs/a.py", "timeout": 10}]},
        ]},
    }))
    rc = euh.main(["--project-settings", str(project_path), "--user-settings", str(user_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "up to date" in out


def test_main_refuses_malformed_user_settings(tmp_path, capsys):
    project_path = tmp_path / "project-settings.json"
    project_path.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "/abs/a.py", "timeout": 10}]},
        ]},
    }))
    user_path = tmp_path / "user-settings.json"
    user_path.write_text(json.dumps(["not", "an", "object"]))
    rc = euh.main(["--apply", "--project-settings", str(project_path), "--user-settings", str(user_path)])
    assert rc == 1
    assert "not a JSON object" in capsys.readouterr().err


def test_main_handles_absent_project_settings_gracefully(tmp_path, capsys):
    project_path = tmp_path / "does-not-exist.json"
    user_path = tmp_path / "user-settings.json"
    user_path.write_text(json.dumps({}))
    rc = euh.main(["--project-settings", str(project_path), "--user-settings", str(user_path)])
    assert rc == 0
    assert "nothing to provision" in capsys.readouterr().err
