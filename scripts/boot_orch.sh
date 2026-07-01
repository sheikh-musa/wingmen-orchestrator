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
SESSION="orch"
CLAUDE_BIN="$(command -v claude || echo /usr/local/bin/claude)"
SLEEP_BETWEEN_RESTARTS=5

log() { echo "[boot_orch] $(date '+%H:%M:%S') $*"; }

# Load env so CLAUDE_CODE_OAUTH_TOKEN is available in the tmux session
if [[ -f "$ORCH_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ORCH_DIR/.env"
    set +a
fi

# Kill any stale session so the bridge sees a clean restart
if tmux has-session -t "$SESSION" 2>/dev/null; then
    log "killing stale '$SESSION' tmux session"
    tmux kill-session -t "$SESSION" || true
    sleep 2
fi

log "starting orch session (claude --resume in tmux '$SESSION')"

# Create session detached so this script stays as the blocking "waiter"
tmux new-session -d -s "$SESSION" -c "$ORCH_DIR" -e "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-}" \
    -- "$CLAUDE_BIN" --resume

log "session '$SESSION' is live — waiting for it to exit"

# Block until session ends; launchd's KeepAlive will restart us when we exit
while tmux has-session -t "$SESSION" 2>/dev/null; do
    sleep "$SLEEP_BETWEEN_RESTARTS"
done

log "session '$SESSION' ended — launchd will restart shortly"
