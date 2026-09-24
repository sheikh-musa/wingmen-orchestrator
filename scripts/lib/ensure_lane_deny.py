#!/usr/bin/env python3
"""ensure_lane_deny.py — enforce a Claude Code tool DENY in a lane's per-worktree
`.claude/settings.local.json`, merge-safely.

WHY: an autonomous lane has no human at the keyboard, so an interactive tool like
`AskUserQuestion` (the multiple-choice menu) HANGS the session — it opened on
cc-substrate on 2026-09-24 and wedged the autocompact-pilot lane (Nazim #42983),
and the same class cost cc-ihsanos a day. Claude Code's permission system removes
a bare-named denied tool from the model's context entirely (docs: permissions.md
"a bare tool name … removes the tool from Claude's context entirely") — no prompt,
no hang. The lane must ask via the bus instead.

DELIVERY: `.claude/settings.local.json` is per-worktree, untracked, and MERGES with
`.claude/settings.json` + user settings (list keys like permissions.deny COMBINE
across files — docs settings.md "Settings precedence"). So writing it here NEVER
clobbers the shared hooks/permissions; it only ADDS the deny entries.

SAFETY: idempotent (re-run adds nothing), atomic (temp + os.replace), and it only
ever ADDS to permissions.deny — it never removes or rewrites anything else in the
file. Fail-LOUD to stderr but exit 0 (a settings-write hiccup must NOT take the lane
down; the deny is best-effort at the launcher and CI/monitoring is the backstop).

Usage: ensure_lane_deny.py --cwd <lane-worktree> [--deny AskUserQuestion ...]
"""
import argparse
import json
import os
import sys
import tempfile

DEFAULT_DENY = ["AskUserQuestion"]

# Wedge sources for a no-human-at-keyboard lane (Nazim #43083 — the dialog/suggestion
# -wedge bundle): the prompt-suggestion widget and the SendFeedback drafts dialog both
# park the pane so lane_nudge cannot wake it. The two keys live at DIFFERENT scopes
# (code.claude.com/docs/en/settings-reference.md), which decides WHERE each is enforced:
#
#   promptSuggestionEnabled  bool False  scope "Any file"  -> enforce in the lane's
#                                        per-worktree settings.local.json (below).
#   feedbackDrafts           str  "off"  scope "User or managed" -> IGNORED in project/
#                                        local settings, so it MUST go in the user-global
#                                        ~/.claude/settings.json (ensure_user_global).
# Writing feedbackDrafts to settings.local.json would be a silent no-op = false
# confidence, so we deliberately split them by scope.
DEFAULT_LOCAL_SETTINGS = {"promptSuggestionEnabled": False}
USER_GLOBAL_SETTINGS = {"feedbackDrafts": "off"}


def _load(path):
    """Return the parsed settings dict, or {} if absent/empty. Raises on malformed
    JSON so we never silently overwrite a file we could not parse."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    if not txt:
        return {}
    return json.loads(txt)  # deliberately not caught: a malformed file must fail loud


def ensure_deny(settings: dict, deny_tools) -> bool:
    """Ensure settings['permissions']['deny'] is a list containing each tool.
    Returns True iff the settings dict was modified. Pure — mutates `settings`."""
    changed = False
    perms = settings.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
        settings["permissions"] = perms
        changed = True
    deny = perms.get("deny")
    if not isinstance(deny, list):
        deny = []
        perms["deny"] = deny
        changed = True
    for tool in deny_tools:
        if tool not in deny:
            deny.append(tool)
            changed = True
    return changed


def ensure_settings(settings: dict, kv) -> bool:
    """Ensure each top-level key in `kv` is set to its enforced value in `settings`.
    Unlike ensure_deny (list-append), a scalar setting is ENFORCED: a differing or
    absent value is overwritten to the desired one, so a lane cannot carry a stale
    value that re-opens the wedge. Every other key is preserved. Returns True iff the
    settings dict was modified. Pure — mutates `settings`."""
    changed = False
    for key, val in kv.items():
        # `key not in` distinguishes absent from a legitimately-False enforced value.
        if key not in settings or settings[key] != val:
            settings[key] = val
            changed = True
    return changed


def ensure_user_global(kv, path) -> bool:
    """Enforce user-scope settings (feedbackDrafts) in the user-global settings file at
    `path` (default ~/.claude/settings.json). Separate from the per-lane local file
    because these keys are ignored at project/local scope. Merge-safe: preserves every
    other key, atomic write, only writes when a value actually changes. Fail-LOUD to
    stderr but NEVER raise/overwrite an unparseable file — returns False on any error so
    a launch is never blocked and no confidence is manufactured. Returns True iff it wrote."""
    try:
        settings = _load(path)
        if not isinstance(settings, dict):
            print(f"ensure_lane_deny: user-global {path} is not a JSON object — refusing to touch it", file=sys.stderr)
            return False
        if ensure_settings(settings, kv):
            _atomic_write(path, settings)
            print(f"ensure_lane_deny: enforced user-global settings {kv} in {path}", file=sys.stderr)
            return True
        return False
    except Exception as e:
        print(f"ensure_lane_deny: FAILED to enforce user-global {kv} in {path}: {e} — leaving it unchanged (CI/monitoring is the backstop)", file=sys.stderr)
        return False


def _atomic_write(path, settings):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings.local.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)  # atomic on the same filesystem
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True, help="the lane's worktree (settings go in <cwd>/.claude/settings.local.json)")
    ap.add_argument("--deny", action="append", default=None, help="tool to deny (repeatable); default: AskUserQuestion")
    ap.add_argument("--user-settings", default=None,
                    help="user-global settings file for user-scope keys (default: ~/.claude/settings.json)")
    args = ap.parse_args(argv)
    deny_tools = args.deny if args.deny else list(DEFAULT_DENY)

    # feedbackDrafts is user-scope-only, so enforce it in the user-global file, not local.
    user_settings_path = os.path.abspath(os.path.expanduser(
        args.user_settings if args.user_settings else "~/.claude/settings.json"))
    ensure_user_global(USER_GLOBAL_SETTINGS, user_settings_path)

    cwd = os.path.abspath(os.path.expanduser(args.cwd))
    if not os.path.isdir(cwd):
        print(f"ensure_lane_deny: cwd not a dir: {cwd} — skipping (lane launches without the deny)", file=sys.stderr)
        return 0
    path = os.path.join(cwd, ".claude", "settings.local.json")
    try:
        settings = _load(path)
        if not isinstance(settings, dict):
            print(f"ensure_lane_deny: {path} is not a JSON object — refusing to touch it", file=sys.stderr)
            return 0
        deny_changed = ensure_deny(settings, deny_tools)
        settings_changed = ensure_settings(settings, DEFAULT_LOCAL_SETTINGS)
        if deny_changed or settings_changed:
            _atomic_write(path, settings)
            print(f"ensure_lane_deny: enforced deny {deny_tools} + local settings {DEFAULT_LOCAL_SETTINGS} in {path}", file=sys.stderr)
        # else: already present — silent, idempotent
    except Exception as e:  # fail LOUD but never block the launch over a settings-write
        print(f"ensure_lane_deny: FAILED to enforce deny in {path}: {e} — lane launches WITHOUT it (CI/monitoring is the backstop)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
