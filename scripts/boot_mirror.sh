#!/usr/bin/env bash
# boot_mirror.sh — Cold standby boot for the Windows PC WSL2 mirror.
#
# By default this starts an orch session WITHOUT any bridges or fleet lanes.
# The mirror watches the Mini and can be activated if the Mini goes down.
#
# Usage:
#   bash scripts/boot_mirror.sh            # cold standby (default, bridges OFF)
#   ACTIVATE=true bash scripts/boot_mirror.sh  # active mode (bridges ON, Mini confirmed down)

set -euo pipefail

ORCH_DIR="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
SESSION="orch-mirror"
CLAUDE_BIN="$(command -v claude || echo '/usr/local/bin/claude')"
SLEEP_BETWEEN_RESTARTS=10
ACTIVATE="${ACTIVATE:-false}"

log() { echo "[boot_mirror] $(date '+%H:%M:%S') $*"; }

# Load base .env
if [[ -f "$ORCH_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ORCH_DIR/.env"
    set +a
    log ".env loaded"
fi

# Layer mirror-specific overrides on top
if [[ -f "$ORCH_DIR/.env.mirror" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ORCH_DIR/.env.mirror"
    set +a
    log ".env.mirror overrides applied (IS_MIRROR=true, bridges OFF)"
fi

# If explicitly activating, flip bridges on
if [[ "$ACTIVATE" == "true" ]]; then
    log "ACTIVATE=true — enabling bridges (Mini takeover mode)"
    export IS_MIRROR=false
    export TG_BRIDGE_ENABLED=true
    export CAI_BRIDGE_ENABLED=true
fi

if [[ "$ACTIVATE" != "true" ]]; then
    log "Cold standby mode — bridges OFF, no fleet lanes"
    log "Set ACTIVATE=true to take over from Mini"
fi

# Kill stale mirror session if any
if tmux has-session -t "$SESSION" 2>/dev/null; then
    log "killing stale '$SESSION' tmux session"
    tmux kill-session -t "$SESSION" || true
    sleep 2
fi

log "starting mirror orch session (claude --resume in tmux '$SESSION')"

tmux new-session -d -s "$SESSION" -c "$ORCH_DIR" \
    -e "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-}" \
    -e "IS_MIRROR=${IS_MIRROR:-true}" \
    -e "TG_BRIDGE_ENABLED=${TG_BRIDGE_ENABLED:-false}" \
    -e "CAI_BRIDGE_ENABLED=${CAI_BRIDGE_ENABLED:-false}" \
    -- "$CLAUDE_BIN" --resume

log "session '$SESSION' is live"

# Block so the caller (Task Scheduler / manual run) sees us as running
while tmux has-session -t "$SESSION" 2>/dev/null; do
    sleep "$SLEEP_BETWEEN_RESTARTS"
done

log "session '$SESSION' ended"
