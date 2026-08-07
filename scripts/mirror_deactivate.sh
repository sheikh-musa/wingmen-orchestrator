#!/usr/bin/env bash
# mirror_deactivate.sh — Return mirror from PRIMARY back to cold standby.
#
# Run this when the Mac Mini is back online.
# Kills the active session (bridges OFF) and re-starts in cold standby mode.

set -euo pipefail

ORCH_DIR="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
SESSION="orch-mirror"

GREEN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GREEN}[deactivate]${NC} $*"; }

info "Killing active mirror session…"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    sleep 2
fi

info "Restarting in cold standby mode (bridges OFF)…"
bash "$ORCH_DIR/scripts/boot_mirror.sh" &

sleep 3
if tmux has-session -t "$SESSION" 2>/dev/null; then
    info "Mirror is back in cold standby. Mini should now be the active node."
else
    info "Session not started (may already be exited). That's fine for cold standby."
fi
