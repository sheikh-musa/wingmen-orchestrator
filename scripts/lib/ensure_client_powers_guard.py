#!/usr/bin/env python3
"""ensure_client_powers_guard.py — enforce the client_powers_guard PreToolUse hook
in a lane's per-worktree `.claude/settings.local.json`, merge-safely.

TG2.0 section 3, Phase A (orch-console #86250). The guard itself
(scripts/hooks/client_powers_guard.py) decides whether a lane may act; this
installer is what makes sure the guard is ACTUALLY WIRED for every lane, on
every host, without per-host hand-editing — the same gap ensure_lane_secrets_
guard.py closed for the secrets guard (bus #53680/#53678): a fresh host, a disk
recovery, or a new OS user would otherwise get zero coverage until someone
remembered to copy a dotfile.

DELIVERY / SAFETY: identical contract to ensure_lane_secrets_guard — the helpers
(_load, _atomic_write, ensure_secrets_guard) are reused rather than copied, so
the two installers can never drift apart. Adds ONE PreToolUse group matched on
`Bash` (the only tool class the guard inspects); never removes or rewrites
anything else; idempotent by command string; atomic write; fail-LOUD to stderr
but exit 0, because a settings-write hiccup must never take a lane down.

Usage: ensure_client_powers_guard.py --cwd <lane-worktree>
"""
from __future__ import annotations

import argparse
import os
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
if _LIB_DIR not in sys.path:  # importable when this file is loaded by path (tests, installers)
    sys.path.insert(0, _LIB_DIR)

from ensure_lane_secrets_guard import _atomic_write, _load, ensure_secrets_guard  # noqa: E402

_ORCH_DIR = os.path.dirname(os.path.dirname(_LIB_DIR))
_VENV_PY = os.path.join(_ORCH_DIR, ".venv", "bin", "python3")
_GUARD_SCRIPT = os.path.join(_ORCH_DIR, "scripts", "hooks", "client_powers_guard.py")

HOOK_MATCHER = "Bash"
HOOK_TIMEOUT = 10


def ensure_client_powers_guard(settings: dict, command: str,
                               matcher: str = HOOK_MATCHER,
                               timeout: int = HOOK_TIMEOUT) -> bool:
    """Ensure settings['hooks']['PreToolUse'] runs `command` on Bash calls.

    Thin alias over the shared, already-tested merge logic — the powers guard
    differs from the secrets guard only in the command it wires. Returns True
    iff `settings` was modified.
    """
    return ensure_secrets_guard(settings, command, matcher=matcher, timeout=timeout)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True,
                    help="the lane's worktree (settings go in <cwd>/.claude/settings.local.json)")
    args = ap.parse_args(argv)

    cwd = os.path.abspath(os.path.expanduser(args.cwd))
    if not os.path.isdir(cwd):
        print(f"ensure_client_powers_guard: cwd not a dir: {cwd} — skipping "
              "(lane launches without the powers guard wired)", file=sys.stderr)
        return 0

    if not os.path.exists(_VENV_PY) or not os.path.exists(_GUARD_SCRIPT):
        print(f"ensure_client_powers_guard: {_VENV_PY} or {_GUARD_SCRIPT} missing on this "
              "host — skipping rather than wiring a hook command that would fail on every "
              "tool call", file=sys.stderr)
        return 0
    command = f"{_VENV_PY} {_GUARD_SCRIPT}"

    path = os.path.join(cwd, ".claude", "settings.local.json")
    try:
        settings = _load(path)
        if not isinstance(settings, dict):
            print(f"ensure_client_powers_guard: {path} is not a JSON object — refusing to touch it",
                  file=sys.stderr)
            return 0
        if ensure_client_powers_guard(settings, command):
            _atomic_write(path, settings)
            print(f"ensure_client_powers_guard: enforced PreToolUse client_powers_guard in {path}",
                  file=sys.stderr)
        # else: already present — silent, idempotent
    except Exception as e:  # fail LOUD but never block the launch over a settings-write
        print(f"ensure_client_powers_guard: FAILED to enforce guard in {path}: {e} — lane "
              "launches WITHOUT the powers guard wired", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
