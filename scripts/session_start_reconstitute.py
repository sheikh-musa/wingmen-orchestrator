#!/usr/bin/env python3
"""session_start_reconstitute.py — SessionStart hook that force-reconstitutes the
Nazim console body on a fresh (re)launch.

WHY THIS EXISTS (op#10721): the console boots via `boot_nazim.sh` → a bare
`exec claude ...` — a fresh session with NO reconstitution step. It depended on
Nazim *remembering* to read his handoff, which repeatedly failed: the operator
kept catching "you reset yourself but did not reconstitute." That breaks the
lane-management tool end-to-end, because every relaunch the tool triggers comes
back amnesiac. This hook makes reconstitution structural, not a matter of memory:
on any fresh startup/clear of the CONSOLE body it injects the newest handoff +
the standing inbox-reconcile directive straight into the session's context.

SCOPING (fail-closed to console): only fires for ORCH_BODY_ROLE=console. Any
other body (hub, a lane in an orchestrator worktree) gets NOTHING — the hub reads
its own handoff, lanes have their own boot. On `resume`/`compact` it stays silent:
context is retained there, so re-injecting the handoff would only bloat the window.

OUTPUT: SessionStart additionalContext JSON (per the hooks contract). Fail-safe —
any error prints nothing and exits 0 so a hook fault never blocks a boot.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys

ORCH_DIR = os.path.expanduser("~/wingmen/orchestrator")
# Fresh-context events only. resume/compact retain context — don't re-inject.
RECONSTITUTE_SOURCES = {"startup", "clear"}
_HANDOFF_GLOB = os.path.join(ORCH_DIR, "reports", "nazim-handoff-*.md")
_MAX_HANDOFF_CHARS = 24000  # bound the injection; the newest handoff is ~6KB


def _body_role() -> str:
    """Resolve ORCH_BODY_ROLE from the live env, falling back to .env."""
    role = os.environ.get("ORCH_BODY_ROLE")
    if role:
        return role.strip()
    env_path = os.path.join(ORCH_DIR, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("ORCH_BODY_ROLE="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _expected_token_fp() -> str | None:
    """The fp the running console SHOULD carry, computed live from .env's
    CLAUDE_CODE_OAUTH_TOKEN — never hardcoded. Matches the boot-string method
    (`printf '%s' "$tok" | shasum | cut -c1-12`): SHA-1 of the token bytes, first
    12 hex chars. Reading it from .env means a token rotation can't leave a stale
    constant here that false-alarms every recycle (the 2026-08-07 07ed/6814 slip).
    """
    env_path = os.path.join(ORCH_DIR, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip("'\"")
                    if tok:
                        return hashlib.sha1(tok.encode()).hexdigest()[:12]
    except OSError:
        pass
    return None


def _newest_handoff() -> str | None:
    paths = glob.glob(_HANDOFF_GLOB)
    if not paths:
        return None
    # Filenames are date-stamped (nazim-handoff-YYYYMMDD[-suffix].md); mtime is the
    # robust "newest" signal even when a same-day evening file supersedes a morning one.
    return max(paths, key=lambda p: os.path.getmtime(p))


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}

    source = (payload.get("source") or "startup").strip()

    # Fail-closed scoping: console body, fresh-context events only.
    if _body_role() != "console":
        return 0
    if source not in RECONSTITUTE_SOURCES:
        return 0

    exp_fp = _expected_token_fp()
    fp_clause = (
        f"expected fp from .env = {exp_fp}" if exp_fp
        else "compare against .env's CLAUDE_CODE_OAUTH_TOKEN"
    )

    handoff_path = _newest_handoff()
    if not handoff_path:
        # No handoff on disk — still nudge to reconcile inboxes rather than stay dark.
        context = (
            "You are **Nazim / console body** and just (re)booted with an empty "
            "context. No nazim-handoff file was found under reports/. Before acting: "
            "reconcile BOTH `operator_log.unprocessed()` AND `agent_messages` "
            "(to_agent='orch-console'); verify your OWN running OAuth token off your "
            f"live process ({fp_clause}); reply to the operator ONLY via "
            "`scripts/nazim_send.sh`."
        )
    else:
        try:
            with open(handoff_path, "r", encoding="utf-8") as fh:
                handoff = fh.read()
        except OSError:
            return 0
        if len(handoff) > _MAX_HANDOFF_CHARS:
            handoff = handoff[:_MAX_HANDOFF_CHARS] + "\n\n…[handoff truncated — read the full file for the rest]"
        rel = os.path.relpath(handoff_path, ORCH_DIR)
        context = (
            "🔁 **CONSOLE RECONSTITUTION (auto-injected on fresh boot — op#10721)**\n\n"
            "You are **Nazim / console body** and just (re)launched with an EMPTY "
            "context. Do NOT act on any operator/bus message until you have absorbed "
            "the handoff below. Then, BEFORE your first outbound action:\n"
            "  1. Verify your OWN running OAuth token off your live process "
            "(walk your shell → claude pid → `ps eww` → CLAUDE_CODE_OAUTH_TOKEN → shasum; "
            f"{fp_clause}). Do not claim a token you have not fingerprinted.\n"
            "  2. Reconcile BOTH `operator_log.unprocessed()` AND `agent_messages` "
            "(to_agent='orch-console'); answer, then stamp "
            "`operator_log.mark_handled_through(<id>)`.\n"
            "  3. Reply to the operator ONLY via `scripts/nazim_send.sh` (a terminal "
            "reply never reaches his phone).\n\n"
            f"--- newest handoff ({rel}) ---\n\n{handoff}"
        )

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never let a hook fault block a boot.
        sys.exit(0)
