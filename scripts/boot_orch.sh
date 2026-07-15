#!/usr/bin/env bash
# boot_orch.sh — Create the orch tmux session and block until it exits.
#
# launchd uses this with KeepAlive=true: when the orch session dies (crash,
# explicit kill, reboot), launchd re-runs this script which kills any stale
# session and starts a fresh one. This ensures the orch bridge is always live.
#
# Run manually:   scripts/boot_orch.sh
# Run in tmux:    tmux new-session -s orch -c ~/wingmen/orchestrator scripts/boot_orch.sh

set -euo pipefail

ORCH_DIR="$HOME/wingmen/orchestrator"
# Resolve claude robustly: under launchd (and non-login SSH) PATH is minimal, so
# `command -v claude` can miss a per-user install (the Mini keeps it at
# ~/.local/bin/claude). Fall back through the known install locations.
CLAUDE_BIN="$(command -v claude || true)"
if [[ -z "$CLAUDE_BIN" ]]; then
    for _c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
        [[ -x "$_c" ]] && CLAUDE_BIN="$_c" && break
    done
fi
# Resolve tmux the same way: under launchd's minimal PATH bare `tmux` is NOT
# found (the Studio keeps it at /opt/homebrew/bin/tmux) — an unresolved `tmux`
# makes the whole boot fail silently, so the KeepAlive job can never bring the
# hub back. Fall back through the known install locations. (2026-07-08: this was
# exactly why the Studio hub could not self-restart.)
TMUX_BIN="$(command -v tmux || true)"
if [[ -z "$TMUX_BIN" ]]; then
    for _t in /opt/homebrew/bin/tmux /usr/local/bin/tmux "$HOME/.local/bin/tmux"; do
        [[ -x "$_t" ]] && TMUX_BIN="$_t" && break
    done
fi
[[ -n "$TMUX_BIN" ]]  || { echo "[boot_orch] FATAL: tmux not found" >&2; exit 1; }
[[ -n "$CLAUDE_BIN" ]] || { echo "[boot_orch] FATAL: claude not found" >&2; exit 1; }
SLEEP_BETWEEN_RESTARTS=5

log() { echo "[boot_orch] $(date '+%H:%M:%S') $*"; }

# Load env so CLAUDE_CODE_OAUTH_TOKEN is available in the tmux session
if [[ -f "$ORCH_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ORCH_DIR/.env"
    set +a
fi

# Scrub ANTHROPIC_API_KEY before launching the hub session (mirrors
# scripts/launch_dangerous_cc.sh): .env carries the key for the orch's own
# direct-API helpers (which re-source .env themselves), but a present
# ANTHROPIC_API_KEY makes `claude` bill metered API-usage instead of the Mac
# Studio's Max subscription — the "Opus 4.8 · Claude API" splash Nazim caught on
# 2026-07-15 (bus #8806). CLAUDE_CODE_OAUTH_TOKEN (also from .env) is passed
# through explicitly below so the session runs on Max.
#
# TWO sources inject the key, both must be scrubbed (2026-07-15, second pass):
#   1. THIS shell's env (the `set -a; source .env` above) — the `unset` here.
#   2. The tmux SERVER's *global* environment — `tmux new-session` copies the
#      server-global env into every new pane, which OVERRIDES the shell unset.
#      A long-lived server (shared with cai/console/lane sessions) that was
#      started with the key in its env re-injects it on every spawn. The first
#      fix (shell unset only) did NOT stop the leak: the respawn still booted
#      metered because the server-global copy won. So we also `setenv -gu` it off
#      the server (below, where a server is guaranteed) and blank it per-session
#      with `-e ANTHROPIC_API_KEY=`.
unset ANTHROPIC_API_KEY

# ORCH-TOPOLOGY-001: session name is body-scoped. The hub boots as `orch`
# (bridge exact-matches =orch); the console body (Nazim, operator's MacBook)
# sets ORCH_TMUX_SESSION=nazim in .env so a non-hub session NEVER claims the
# hub's name (the leftover `orch` name is how the 07-04 pen-(iv) slip happened).
SESSION="${ORCH_TMUX_SESSION:-orch}"

# ADOPT an existing live session — do NOT kill it. If `orch` already exists
# (an operator/break-glass manual bring-up, or a still-running prior instance),
# just supervise it. This lets the launchd KeepAlive job be (re)loaded WITHOUT
# interrupting a live hub mid-task — the reason a naive `launchctl bootstrap`
# used to blip the operator's session. A fresh session is created only when none
# exists; KeepAlive re-runs this script after the session actually dies.
if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
    log "adopting existing live '$SESSION' session (no restart)"
else
    log "starting orch session (claude --continue in tmux '$SESSION')"
    # Scrub ANTHROPIC_API_KEY off the tmux SERVER-global env so new-session does
    # not re-inject it into the pane (see the block above). start-server first so
    # setenv has a target even on a cold boot; both are harmless no-ops otherwise.
    "$TMUX_BIN" start-server 2>/dev/null || true
    "$TMUX_BIN" setenv -gu ANTHROPIC_API_KEY 2>/dev/null || true
    # --continue (NOT --resume): --resume opens an interactive session PICKER
    # that, unattended in a detached tmux, hangs at the menu instead of coming
    # up live — so a launchd/manual restart would leave the hub stuck at a
    # chooser. --continue auto-resumes the most-recent conversation in this dir
    # (the hub's own ongoing session) → an unattended restart returns LIVE with
    # context. `-e ANTHROPIC_API_KEY=` blanks it for this pane belt-and-suspenders.
    "$TMUX_BIN" new-session -d -s "$SESSION" -c "$ORCH_DIR" \
        -e "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-}" \
        -e "ANTHROPIC_API_KEY=" \
        -- "$CLAUDE_BIN" --dangerously-skip-permissions --continue --model claude-opus-4-8
fi

log "session '$SESSION' is live — waiting for it to exit"

# Block until session ends; launchd's KeepAlive will restart us when we exit
while "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; do
    sleep "$SLEEP_BETWEEN_RESTARTS"
done

log "session '$SESSION' ended — launchd will restart shortly"
