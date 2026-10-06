#!/usr/bin/env python3
"""ensure_lane_secrets_guard.py — enforce the secrets_transcript_guard PreToolUse
hook in a lane's per-worktree `.claude/settings.local.json`, merge-safely.

WHY (bus #53680/#53678, orch-console routing cc-fleet-health's own ask): today
secrets_transcript_guard is wired only in each HOST's own user-global
~/.claude/settings.json — hand-maintained per host/OS-user, not committed, not
enforced by anything in code. Audited directly this pass: it is in fact already
correctly and identically wired on both current hosts (the Mini, gzb), and no
lane's settings.local.json shadows/overrides it — so it was NOT the cause of the
#53636/#53638 incidents cc-fleet-health's sweep caught. Both of those were
tool=Bash, pattern classes telegram-bot-token/postgres-dsn, caught by the
PostToolUse secrets_output_scanner — consistent with an output-only leak (the
command TEXT was clean; a secret surfaced only in what the command printed at
runtime, e.g. an API echoing a token back), which a PreToolUse hook structurally
cannot see before the command ever runs. That class needs the reactive scanner
and always will; no amount of PreToolUse wiring changes it.

What IS a real, if currently latent, gap: the user-global file is a hand-
maintained per-host dotfile, not enforced by anything. A fresh host, a disk
recovery (reference_fleet_recovery_from_registry_after_disk_crash), or a new OS
user gets ZERO proactive coverage until someone remembers to copy the right
absolute paths into a new ~/.claude/settings.json. This closes THAT gap in code:
every lane, on every host, gets the guard the moment it goes through the shared
launcher — no per-host manual setup, no per-lane opt-in.

DELIVERY: same shape as ensure_lane_deny.py — per-worktree, untracked, merge-safe
(adds a hook GROUP, never removes/rewrites an existing one). Multiple PreToolUse
entries for the same tool from different settings scopes (user-global + this
local file) all fire — this is additive defense-in-depth on top of the existing
user-global wiring, never a replacement for it.

PORTABILITY: the venv/script paths are resolved from THIS file's own location
(it only ever runs from inside the orchestrator's live checkout, since that is
the only place a lane's VENV_PY ever points), not hardcoded — so the exact same
code produces the right absolute path on any host, present or future, with zero
per-host editing.

SAFETY: idempotent (matches by command string; re-run adds nothing), atomic
(temp + os.replace), and ONLY ever adds to hooks.PreToolUse — never removes or
rewrites anything else in the file. Fail-LOUD to stderr but exit 0 (a settings-
write hiccup must never take the lane down; the user-global hook + the
PostToolUse backstop both still apply regardless).

Usage: ensure_lane_secrets_guard.py --cwd <lane-worktree>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

_ORCH_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VENV_PY = os.path.join(_ORCH_DIR, ".venv", "bin", "python3")
_GUARD_SCRIPT = os.path.join(_ORCH_DIR, "scripts", "hooks", "secrets_transcript_guard.py")

HOOK_MATCHER = "Bash|Read|Grep|Edit|MultiEdit|Write|NotebookEdit"
HOOK_TIMEOUT = 10


def _load(path: str) -> dict:
    """Return the parsed settings dict, or {} if absent/empty. Raises on malformed
    JSON so we never silently overwrite a file we could not parse."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    if not txt:
        return {}
    return json.loads(txt)  # deliberately not caught: a malformed file must fail loud


def _atomic_write(path: str, settings: dict) -> None:
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


def ensure_secrets_guard(settings: dict, command: str, matcher: str = HOOK_MATCHER,
                          timeout: int = HOOK_TIMEOUT) -> bool:
    """Ensure settings['hooks']['PreToolUse'] contains a group running `command`.
    Matches by command string alone (not matcher) so re-running never duplicates
    the entry even if the matcher is later widened. Returns True iff `settings`
    was modified. Pure — mutates `settings` in place, preserves every other key."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    pre = hooks.get("PreToolUse")
    if not isinstance(pre, list):
        pre = []
        hooks["PreToolUse"] = pre
    for group in pre:
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks", []):
            if isinstance(h, dict) and h.get("command") == command:
                return False  # already present — idempotent no-op
    pre.append({
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command, "timeout": timeout}],
    })
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True,
                     help="the lane's worktree (settings go in <cwd>/.claude/settings.local.json)")
    args = ap.parse_args(argv)

    cwd = os.path.abspath(os.path.expanduser(args.cwd))
    if not os.path.isdir(cwd):
        print(f"ensure_lane_secrets_guard: cwd not a dir: {cwd} — skipping "
              "(lane launches without this extra layer)", file=sys.stderr)
        return 0

    if not os.path.exists(_VENV_PY) or not os.path.exists(_GUARD_SCRIPT):
        print(f"ensure_lane_secrets_guard: {_VENV_PY} or {_GUARD_SCRIPT} missing on this host — "
              "skipping rather than wiring a hook command that would fail on every tool call "
              "(the user-global ~/.claude/settings.json wiring + the PostToolUse backstop still apply)",
              file=sys.stderr)
        return 0
    command = f"{_VENV_PY} {_GUARD_SCRIPT}"

    path = os.path.join(cwd, ".claude", "settings.local.json")
    try:
        settings = _load(path)
        if not isinstance(settings, dict):
            print(f"ensure_lane_secrets_guard: {path} is not a JSON object — refusing to touch it",
                  file=sys.stderr)
            return 0
        if ensure_secrets_guard(settings, command):
            _atomic_write(path, settings)
            print(f"ensure_lane_secrets_guard: enforced PreToolUse secrets_transcript_guard in {path}",
                  file=sys.stderr)
        # else: already present — silent, idempotent
    except Exception as e:  # fail LOUD but never block the launch over a settings-write
        print(f"ensure_lane_secrets_guard: FAILED to enforce guard in {path}: {e} — lane launches "
              "WITHOUT this extra layer (user-global wiring + PostToolUse backstop are the fallback)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
