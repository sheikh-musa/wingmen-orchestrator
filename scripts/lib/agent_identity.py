"""agent_identity.py — the ONE place "who am I, for attribution" is resolved.

Extracted from bus_send.py's resolve_from_agent (orch-console bus #47221,
after Nazim caught scripts/asks_triage.py stamping triaged_by from
$ORCH_AGENT_ID — every process that sources the orchestrator .env inherits
ORCH_AGENT_ID=orch-console as fleet-wide-exported noise, so any lane running
asks_triage.py got misattributed to the console. 17 oeh + 2 angullia rows
were corrected after this fix landed. bus_send.py already had the correct,
battle-tested fail-closed resolver; this just factors it out so
asks_triage.py / asks_open.py / anything else that stamps an attribution
column can reuse it instead of re-deriving (and re-breaking) the same logic.
"""
from __future__ import annotations

import json
import os
import subprocess


class IdentityError(RuntimeError):
    pass


def _in_console_tmux_session(env: dict) -> bool:
    """True iff THIS process is actually running inside the tmux session
    named by ORCH_TMUX_SESSION in *env* (the real Nazim console session,
    canonically tmux session 'nazim' per this repo's own topology doctrine
    -- "Never name a non-hub session 'orch'"). Verified by querying tmux
    directly, not by trusting any env var: an env flag is forgeable by any
    shell that sets it (console bus #85145), but faking the actual live
    tmux session name requires actually running inside a session with that
    exact name -- a much higher bar, and one this repo already treats as
    load-bearing identity elsewhere.
    Fail-safe: not in tmux, no ORCH_TMUX_SESSION, tmux missing/erroring, or
    a timeout all return False -- this can only ADD a correct console match,
    never fabricate one on an error."""
    if not env.get("TMUX"):
        return False
    target = env.get("ORCH_TMUX_SESSION")
    if not target:
        return False
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "#S"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        return False
    return r.stdout.strip() == target


def resolve_agent_id(env: dict) -> str:
    """Resolve "who am I" for attribution. Fail closed, never guess.

    Order (matches bus_send.py's resolve_from_agent):
      1. CC_BASE_AGENT_ID — interactive fleet lane / this Claude Code session.
      2. AGENT_ID         — daemon/watchdog launchers.
      3. ORCH_AGENT_ID, but ONLY when ORCH_BODY_ROLE=console AND (running
         inside the actual 'nazim' tmux session, per _in_console_tmux_session,
         OR ORCH_CONSOLE_BOOT=1 -- see below). ORCH_AGENT_ID is otherwise
         fleet-wide-exported .env noise and must NEVER be trusted blindly.

    Why rule 3 needs more than ORCH_BODY_ROLE=console (bus #85112/#85131,
    2026-10-30 -- a RECURRENCE of this same docstring's own bug #47221):
    ORCH_BODY_ROLE=console is a STATIC DEFAULT baked into the shared
    orchestrator .env, so EVERY process that sources that .env for DB creds
    -- not just the real Nazim console session -- inherits it ambiently,
    identically to the ORCH_AGENT_ID noise this rule already claims to
    guard against. Verified live: cc-fleet-health sourcing the shared .env
    in an ordinary ops shell (no CC_BASE_AGENT_ID set) resolved as
    'orch-console' and posted a real bus row under that identity (#85124).

    An EARLIER version of this fix required ORCH_CONSOLE_BOOT=1 alone (a
    marker boot_nazim.sh exports directly, never sourced from .env).
    Console caught two problems with that (bus #85145) before it merged:
    (a) the LIVE console session predates the fix and has no such marker --
    merging it as-is would have cut off every bus_send from the running
    console until a restart, mid-session; (b) a bare env flag is still
    forgeable by any shell that chooses to set it. The tmux-session check
    covers the live session with zero restart (it's already running in
    'nazim') and is a harder-to-fake signal; ORCH_CONSOLE_BOOT=1 is kept
    as a second, simpler path so a *future* boot_nazim.sh run still works
    even if its tmux session were ever renamed.
    Deliberately NOT keyed on CC_BASE_AGENT_ID's absence (the signal
    scripts/hooks/console_irsyad_guard.py already uses to distinguish
    console from lanes) -- that signal stays exactly as-is, unchanged by
    this fix, specifically so that guard's behavior cannot regress.
    Raises IdentityError (never guesses / never defaults to 'orch-console')
    if none of these resolve.
    """
    v = env.get("CC_BASE_AGENT_ID")
    if v:
        return v
    v = env.get("AGENT_ID")
    if v:
        return v
    if env.get("ORCH_BODY_ROLE") == "console" and (
        env.get("ORCH_CONSOLE_BOOT") == "1" or _in_console_tmux_session(env)
    ):
        v = env.get("ORCH_AGENT_ID")
        if v:
            return v
    raise IdentityError(
        "cannot resolve agent identity: set CC_BASE_AGENT_ID or AGENT_ID in "
        "the environment, or pass an explicit identity override. Refusing "
        "to guess (see reference_agent_id_not_orch_agent_id_for_identity)."
    )


def _lane_identity_marker_path(cwd: str) -> str | None:
    """Absolute-git-dir path for *cwd*'s repo/worktree, or None if `git` can't
    resolve one (not a repo, git missing, timeout). Pure best-effort lookup —
    never raises."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    git_dir = r.stdout.strip()
    return os.path.join(git_dir, "cc-lane-identity.json") if git_dir else None


def read_lane_identity_marker(cwd: str) -> dict:
    """Read the per-worktree marker launch_dangerous_cc.sh writes at launch
    (bus #68342). Returns {} on any miss/error (no repo at cwd, no marker,
    unreadable, malformed) — callers treat that identically to "unknown"."""
    path = _lane_identity_marker_path(cwd)
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_base_agent_id_for_hook(cwd: str | None, env: dict | None = None) -> str:
    """Resolve "who am I" (base_agent_id only) from inside a Claude Code
    PreToolUse/PostToolUse hook subprocess.

    WHY A SEPARATE RESOLVER (bus #68342, recurring): a hook subprocess does
    not reliably inherit CC_BASE_AGENT_ID from the claude process that spawns
    it, even though CC_BASE_AGENT_ID is exported in that process's own
    environment (confirmed: cc-irsyad-coord's attest-read allow-path in
    client_media_read_guard.py resolved to empty inside the hook, blocking a
    Read it was authorized to make, twice in one session). resolve_agent_id()
    above is for a long-lived process reading its OWN env and should stay
    env-only; this is specifically for a short-lived hook subprocess that
    needs a fallback when that inheritance fails.

    Order:
      1. CC_BASE_AGENT_ID / AGENT_ID from *env* — unchanged fast path,
         correct whenever the hook DOES inherit it (zero behaviour change).
      2. The per-worktree marker launch_dangerous_cc.sh writes at launch
         (see read_lane_identity_marker) — filesystem-only, no env/DB
         dependency, keyed by *cwd* (the hook's own reported cwd from its
         JSON payload, NOT the path of whatever file/tool it's inspecting).

    Returns "" (never raises) if neither resolves — callers must treat ""
    exactly as before this fallback existed (fail-closed / deny), so this
    function can only ADD a correct resolution, never produce an incorrect
    one that unlocks something the env-only path wouldn't have.
    """
    env = env if env is not None else os.environ
    v = (env.get("CC_BASE_AGENT_ID") or env.get("AGENT_ID") or "").strip()
    if v:
        return v
    if not cwd:
        return ""
    return str(read_lane_identity_marker(cwd).get("base_agent_id") or "").strip()
