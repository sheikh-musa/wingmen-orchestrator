#!/usr/bin/env bash
# boot_fleet_health_launchd.sh — launchd wrapper for cc-fleet-health (bus #82910,
# console-approved RunAtLoad+KeepAlive plist, mirrors dev.wingmen.cc-orch.plist's
# boot_orch.sh pattern).
#
# ADOPTS an existing live 'fleet-health' tmux session — never kills one. This lets
# the launchd job be (re)loaded or re-triggered without interrupting a live SRE
# turn (same reasoning as boot_orch.sh's own adopt-not-kill fix). A fresh session
# is created only when none exists; KeepAlive re-runs this script after the
# session actually dies, which then creates a fresh one.
#
# Run in tmux manually (unchanged, still the documented path):
#   tmux new-session -d -s fleet-health -c ~/wingmen/fleet-health \
#       ~/wingmen/fleet-health/boot_fleet_health.sh
set -euo pipefail

SESSION="fleet-health"
FH_DIR="$HOME/wingmen/fleet-health"
BOOT_SCRIPT="$FH_DIR/boot_fleet_health.sh"
SLEEP_BETWEEN_RESTARTS=5

log() { echo "[boot_fleet_health_launchd] $(date '+%H:%M:%S') $*"; }

# Resolve tmux robustly: under launchd's minimal PATH bare `tmux` is NOT found
# (same gap boot_orch.sh hit and fixed on 2026-07-08).
TMUX_BIN="$(command -v tmux || true)"
if [[ -z "$TMUX_BIN" ]]; then
    for _t in /opt/homebrew/bin/tmux /usr/local/bin/tmux "$HOME/.local/bin/tmux"; do
        [[ -x "$_t" ]] && TMUX_BIN="$_t" && break
    done
fi
[[ -n "$TMUX_BIN" ]] || { echo "[boot_fleet_health_launchd] FATAL: tmux not found" >&2; exit 1; }
[[ -x "$BOOT_SCRIPT" ]] || { echo "[boot_fleet_health_launchd] FATAL: $BOOT_SCRIPT not found/executable" >&2; exit 1; }

if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
    log "adopting existing live '$SESSION' session (no restart)"
else
    log "starting '$SESSION' session ($BOOT_SCRIPT)"
    "$TMUX_BIN" new-session -d -s "$SESSION" -c "$FH_DIR" -- "$BOOT_SCRIPT"
fi

# Block until the session ends; launchd's KeepAlive restarts us when we exit,
# which then creates a fresh session (the branch above).
while "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; do
    sleep "$SLEEP_BETWEEN_RESTARTS"
done

log "session '$SESSION' ended — launchd will restart shortly"
