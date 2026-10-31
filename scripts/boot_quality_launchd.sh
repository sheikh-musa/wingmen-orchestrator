#!/usr/bin/env bash
# boot_quality_launchd.sh — launchd wrapper for cc-quality (bus #82910,
# console-approved RunAtLoad+KeepAlive plist, mirrors dev.wingmen.cc-orch.plist's
# boot_orch.sh pattern).
#
# ADOPTS an existing live 'quality' tmux session — never kills one. A fresh
# session is created only when none exists; KeepAlive re-runs this script after
# the session actually dies, which then creates a fresh one.
#
# Run in tmux manually (unchanged, still the documented path):
#   tmux new-session -d -s quality -c ~/wingmen/quality \
#       ~/wingmen/orchestrator/scripts/boot_quality.sh
set -euo pipefail

SESSION="quality"
Q_DIR="$HOME/wingmen/quality"
BOOT_SCRIPT="$HOME/wingmen/orchestrator/scripts/boot_quality.sh"
SLEEP_BETWEEN_RESTARTS=5

log() { echo "[boot_quality_launchd] $(date '+%H:%M:%S') $*"; }

TMUX_BIN="$(command -v tmux || true)"
if [[ -z "$TMUX_BIN" ]]; then
    for _t in /opt/homebrew/bin/tmux /usr/local/bin/tmux "$HOME/.local/bin/tmux"; do
        [[ -x "$_t" ]] && TMUX_BIN="$_t" && break
    done
fi
[[ -n "$TMUX_BIN" ]] || { echo "[boot_quality_launchd] FATAL: tmux not found" >&2; exit 1; }
[[ -x "$BOOT_SCRIPT" ]] || { echo "[boot_quality_launchd] FATAL: $BOOT_SCRIPT not found/executable" >&2; exit 1; }

if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
    log "adopting existing live '$SESSION' session (no restart)"
else
    log "starting '$SESSION' session ($BOOT_SCRIPT)"
    "$TMUX_BIN" new-session -d -s "$SESSION" -c "$Q_DIR" -- "$BOOT_SCRIPT"
fi

while "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; do
    sleep "$SLEEP_BETWEEN_RESTARTS"
done

log "session '$SESSION' ended — launchd will restart shortly"
