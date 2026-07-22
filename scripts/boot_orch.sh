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

# Force the Mac Mini's Claude Max subscription, never metered API billing.
# .env carries a live ANTHROPIC_API_KEY; if it survives into the environment,
# `claude` silently routes this session — the single most continuously-running
# body in the fleet — through the paid API instead of Max (MEMORY: "Lane .env
# ANTHROPIC_API_KEY forces METERED API"). Every sibling launcher (boot_nazim.sh,
# boot_cai.sh, boot_fleet_health.sh, launch_dangerous_cc.sh) already strips it
# here; boot_orch.sh was the lone omission (fable substrate scan, critic #2).
# Keep CLAUDE_CODE_OAUTH_TOKEN — tmux/headless can't read the GUI-login OAuth
# from the Keychain and needs it for auth.
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
    # --continue (NOT --resume): --resume opens an interactive session PICKER
    # that, unattended in a detached tmux, hangs at the menu instead of coming
    # up live — so a launchd/manual restart would leave the hub stuck at a
    # chooser. --continue auto-resumes the most-recent conversation in this dir
    # (the hub's own ongoing session) → an unattended restart returns LIVE with
    # context.
    "$TMUX_BIN" new-session -d -s "$SESSION" -c "$ORCH_DIR" -e "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-}" \
        -- "$CLAUDE_BIN" --dangerously-skip-permissions --continue --model claude-opus-4-8
fi

log "session '$SESSION' is live — waiting for it to exit"

# Block until session ends; launchd's KeepAlive will restart us when we exit
while "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; do
    sleep "$SLEEP_BETWEEN_RESTARTS"
done

log "session '$SESSION' ended — launchd will restart shortly"
