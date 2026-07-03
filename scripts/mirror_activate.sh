#!/usr/bin/env bash
# mirror_activate.sh — Switch the mirror from cold standby to PRIMARY.
#
# Run this on the Windows PC (WSL2) when the Mac Mini is confirmed DOWN.
# It:
#   1. Kills the cold standby orch-mirror session
#   2. Re-starts it with ACTIVATE=true (bridges ON)
#
# To deactivate and return to cold standby (Mini is back up):
#   bash scripts/mirror_deactivate.sh

set -euo pipefail

ORCH_DIR="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
SESSION="orch-mirror"
CLAUDE_BIN="$(command -v claude || echo '/usr/local/bin/claude')"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[activate]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

warn "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
warn " MIRROR ACTIVATION — only do this if Mini is DOWN"
warn " This will enable Telegram bridges on this PC."
warn " Running BOTH will cause duplicate bot replies."
warn "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
read -r -p "Is the Mac Mini confirmed unreachable? [y/N] " confirm
[[ "$confirm" =~ ^[yY]$ ]] || { info "Aborted."; exit 0; }

# Kill standby session
if tmux has-session -t "$SESSION" 2>/dev/null; then
    info "Killing cold standby session…"
    tmux kill-session -t "$SESSION"
    sleep 2
fi

# Re-boot with bridges ON
info "Starting active session (bridges ON)…"
ACTIVATE=true bash "$ORCH_DIR/scripts/boot_mirror.sh" &

sleep 3
if tmux has-session -t "$SESSION" 2>/dev/null; then
    info "Mirror is now PRIMARY. Attach: tmux attach -t $SESSION"
    info "To return to standby when Mini recovers: scripts/mirror_deactivate.sh"
else
    error "Session did not start. Check logs."
    exit 1
fi
