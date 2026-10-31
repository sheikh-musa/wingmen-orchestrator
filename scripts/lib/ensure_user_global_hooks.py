#!/usr/bin/env python3
"""ensure_user_global_hooks.py — close the "new guard never reaches off-tree bodies"
gap, in code instead of by hand.

WHY (orch-console bus #86124, 2026-10-31): live_checkout_edit_guard (PR#713) was wired
into orchestrator's OWN project-level .claude/settings.json, which only loads for a
session whose Claude Code project root IS ~/wingmen/orchestrator itself. Every other
body (cc-fleet-health included — this incident) runs with its project root elsewhere
(~/wingmen/fleet-health, a lane's own project dir, ...) and relies entirely on its
hand-maintained, per-host, NOT-committed ~/.claude/settings.json to pick up orchestrator's
hooks at all. The guard fired for nobody off-tree because nobody remembered to copy the
new entry into that dotfile — the exact same class of gap ensure_lane_secrets_guard.py
closed for secrets_transcript_guard alone (bus #53680), just for a hook that landed
AFTER that one-off fix and was never added to it.

This generalizes the fix: read EVERY PreToolUse hook command from orchestrator's own
project-level .claude/settings.json (the single source of truth for which guards
SHOULD apply fleet-wide), resolve $CLAUDE_PROJECT_DIR to this repo's absolute path (a
user-global file has no project context to expand that variable against), and
idempotently merge any missing one into the caller's own ~/.claude/settings.json —
never removing or rewriting anything else there (model, permissions, plugins, etc. —
that file is far more than hooks).

SAFETY: --check (default) only REPORTS which commands are missing, writes nothing.
--apply performs the same atomic (temp + os.replace) merge-only write
ensure_lane_secrets_guard.py uses. Matches by resolved command string, so re-running
is always a no-op once applied — safe to run on every boot/health pass.

NOT a replacement for ensure_lane_secrets_guard.py (that one provisions a LANE's
per-worktree settings.local.json at launch time, a different file/lifecycle); this one
provisions a BODY's own user-global settings.json, meant to be run directly by that
body (or at its boot) against itself — there is no "launch a user" step to hook into.

Usage:
  ensure_user_global_hooks.py                      # default: report only, writes nothing
  ensure_user_global_hooks.py --apply               # merge missing hooks in, write
  ensure_user_global_hooks.py --apply --user-settings <path>   # non-default target (tests)
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile

_ORCH_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROJECT_SETTINGS = os.path.join(_ORCH_DIR, ".claude", "settings.json")
_DEFAULT_USER_SETTINGS = os.path.expanduser("~/.claude/settings.json")


def _load(path: str) -> dict:
    """Return the parsed settings dict, or {} if absent/empty. Raises on malformed
    JSON so we never silently overwrite a file we could not parse."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    if not txt:
        return {}
    return json.loads(txt)


def _atomic_write(path: str, settings: dict) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)  # atomic on the same filesystem
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def resolve_project_dir_var(command: str) -> str:
    """$CLAUDE_PROJECT_DIR only expands against whatever project is active; a
    user-global file has no such context, so it must be baked in literally here."""
    return command.replace("$CLAUDE_PROJECT_DIR", _ORCH_DIR).replace(
        '"$CLAUDE_PROJECT_DIR"', f'"{_ORCH_DIR}"'
    )


def project_pretooluse_commands(project_settings: dict) -> list[tuple[str, str, int]]:
    """[(matcher, resolved_command, timeout), ...] for every PreToolUse hook entry in
    orchestrator's own project-level settings.json — the source of truth for which
    guards are meant to apply fleet-wide. Skips any non-command hook type (none exist
    today, but a future 'type' we don't resolve a command for must never be silently
    treated as a command)."""
    out: list[tuple[str, str, int]] = []
    for group in (project_settings.get("hooks") or {}).get("PreToolUse", []):
        if not isinstance(group, dict):
            continue
        matcher = group.get("matcher", "*")
        for h in group.get("hooks", []):
            if not isinstance(h, dict) or h.get("type") != "command":
                continue
            cmd = h.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                continue
            out.append((matcher, resolve_project_dir_var(cmd), int(h.get("timeout", 10))))
    return out


def _normalize_command(command: str) -> tuple[str, ...]:
    """Tokenize a shell command so quoting style never affects equality --
    `"$CLAUDE_PROJECT_DIR"/x` (project settings' own style), `/abs/x` (unquoted),
    and `"/abs/x"` (fully quoted) all normalize to the same token once
    $CLAUDE_PROJECT_DIR is resolved, because they are the same command to a
    shell. Caught live (orch-console bus #86124 follow-up): the hand-maintained
    user-global file quotes some entries differently than the project file's
    own `"$CLAUDE_PROJECT_DIR"/...` style, and a naive exact-string compare
    both false-flagged already-present hooks as missing AND would have
    --apply'd exact duplicates of them. Falls back to the raw string on a
    shlex parse error (an unbalanced quote) rather than crashing -- two
    malformed commands simply won't match, which is the safe direction here.
    """
    try:
        return tuple(shlex.split(command))
    except ValueError:
        return (command,)


def existing_pretooluse_commands(user_settings: dict) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for group in (user_settings.get("hooks") or {}).get("PreToolUse", []):
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks", []):
            if isinstance(h, dict) and isinstance(h.get("command"), str):
                out.add(_normalize_command(h["command"]))
    return out


def missing_commands(
    project_settings: dict, user_settings: dict
) -> list[tuple[str, str, int]]:
    """Entries present in the project file, resolved, but absent (compared by
    normalized command TOKENS, not the raw string -- see _normalize_command)
    from the user-global file."""
    present = existing_pretooluse_commands(user_settings)
    return [
        (matcher, cmd, timeout)
        for matcher, cmd, timeout in project_pretooluse_commands(project_settings)
        if _normalize_command(cmd) not in present
    ]


def merge_missing(user_settings: dict, missing: list[tuple[str, str, int]]) -> bool:
    """Append one PreToolUse group per missing (matcher, command, timeout). Mutates
    user_settings in place; never touches any other key. Returns True iff anything
    was added."""
    if not missing:
        return False
    hooks = user_settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        user_settings["hooks"] = hooks
    pre = hooks.get("PreToolUse")
    if not isinstance(pre, list):
        pre = []
        hooks["PreToolUse"] = pre
    for matcher, cmd, timeout in missing:
        pre.append({
            "matcher": matcher,
            "hooks": [{"type": "command", "command": cmd, "timeout": timeout}],
        })
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the merge; default is report-only")
    ap.add_argument("--user-settings", default=_DEFAULT_USER_SETTINGS,
                     help="override the user-global settings path (tests only)")
    ap.add_argument("--project-settings", default=_PROJECT_SETTINGS,
                     help="override orchestrator's project settings path (tests only)")
    args = ap.parse_args(argv)

    try:
        project_settings = _load(args.project_settings)
    except Exception as e:
        print(f"ensure_user_global_hooks: could not read {args.project_settings}: {e} — "
              "refusing to guess which hooks should apply", file=sys.stderr)
        return 1
    if not project_settings:
        print(f"ensure_user_global_hooks: {args.project_settings} is empty/absent — nothing to provision",
              file=sys.stderr)
        return 0

    try:
        user_settings = _load(args.user_settings)
    except Exception as e:
        print(f"ensure_user_global_hooks: could not read {args.user_settings}: {e} — "
              "refusing to touch a file we could not parse", file=sys.stderr)
        return 1
    if not isinstance(user_settings, dict):
        print(f"ensure_user_global_hooks: {args.user_settings} is not a JSON object — refusing to touch it",
              file=sys.stderr)
        return 1

    missing = missing_commands(project_settings, user_settings)
    if not missing:
        print("ensure_user_global_hooks: up to date — every orchestrator PreToolUse hook "
              f"is already present in {args.user_settings}")
        return 0

    for matcher, cmd, _timeout in missing:
        print(f"ensure_user_global_hooks: MISSING ({matcher}): {cmd}")

    if not args.apply:
        print(f"ensure_user_global_hooks: {len(missing)} hook(s) missing from {args.user_settings} "
              "— re-run with --apply to add them (report-only, nothing written)")
        return 1

    merge_missing(user_settings, missing)
    _atomic_write(args.user_settings, user_settings)
    print(f"ensure_user_global_hooks: added {len(missing)} missing hook(s) to {args.user_settings}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
