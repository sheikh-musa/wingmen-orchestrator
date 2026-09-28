"""Lint test for .claude/settings.json (bus #44518): a hook command hardcoded to a
Mac Mini absolute path (/Users/sheikhmusa/...) breaks on any other host that checks
out this repo (e.g. the gzb hub, /home/<user>/...), including the SessionStart
reconstitution and PreCompact digest hooks -- a real loss on that host's own resets.
Hook commands must reference "$CLAUDE_PROJECT_DIR" instead so they resolve wherever
the repo is checked out."""
import json
import pathlib
import re

_SETTINGS_PATH = pathlib.Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
_ABS_HOME_PATH_RE = re.compile(r"/Users/[^/\s\"]+/|/home/[^/\s\"]+/")


def _iter_hook_commands(settings: dict):
    for hook_matchers in settings.get("hooks", {}).values():
        for matcher_entry in hook_matchers:
            for hook in matcher_entry.get("hooks", []):
                if hook.get("type") == "command":
                    yield hook["command"]


def test_settings_json_is_valid_json():
    json.loads(_SETTINGS_PATH.read_text())


def test_no_hook_command_hardcodes_an_absolute_user_home_path():
    settings = json.loads(_SETTINGS_PATH.read_text())
    commands = list(_iter_hook_commands(settings))
    assert commands, "expected at least one hook command in .claude/settings.json"
    offenders = [c for c in commands if _ABS_HOME_PATH_RE.search(c)]
    assert offenders == [], (
        "hook command(s) hardcode a host-specific absolute path -- use "
        '"$CLAUDE_PROJECT_DIR" so the hook resolves on any host: ' + repr(offenders)
    )


def test_every_hook_command_is_rooted_at_claude_project_dir():
    settings = json.loads(_SETTINGS_PATH.read_text())
    commands = list(_iter_hook_commands(settings))
    for command in commands:
        assert "$CLAUDE_PROJECT_DIR" in command, (
            f"hook command does not reference $CLAUDE_PROJECT_DIR: {command!r}"
        )
