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
    args = ap.parse_args(argv)
    deny_tools = args.deny if args.deny else list(DEFAULT_DENY)

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
        if ensure_deny(settings, deny_tools):
            _atomic_write(path, settings)
            print(f"ensure_lane_deny: enforced deny {deny_tools} in {path}", file=sys.stderr)
        # else: already present — silent, idempotent
    except Exception as e:  # fail LOUD but never block the launch over a settings-write
        print(f"ensure_lane_deny: FAILED to enforce deny in {path}: {e} — lane launches WITHOUT it (CI/monitoring is the backstop)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
