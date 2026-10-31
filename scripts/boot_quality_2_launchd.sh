#!/usr/bin/env bash
# boot_quality_2_launchd.sh — launchd wrapper for cc-quality-2 (orch-console #87390).
#
# Mirrors boot_quality_launchd.sh exactly: ADOPTS an existing live 'quality-2' tmux
# session, never kills one. KeepAlive re-runs this after the session actually dies,
# which then creates a fresh one.
set -euo pipefail

SESSION="quality-2"
Q_DIR="$HOME/wingmen/quality-2"
BOOT_SCRIPT="$Q_DIR/boot_quality_2.sh"
SLEEP_BETWEEN_RESTARTS=5

log() { echo "[boot_quality_2_launchd] $(date '+%H:%M:%S') $*"; }

TMUX_BIN="$(command -v tmux || true)"
if [[ -z "$TMUX_BIN" ]]; then
    for _t in /opt/homebrew/bin/tmux /usr/local/bin/tmux "$HOME/.local/bin/tmux"; do
        [[ -x "$_t" ]] && TMUX_BIN="$_t" && break
    done
fi
[[ -n "$TMUX_BIN" ]] || { echo "[boot_quality_2_launchd] FATAL: tmux not found" >&2; exit 1; }
[[ -x "$BOOT_SCRIPT" ]] || { echo "[boot_quality_2_launchd] FATAL: $BOOT_SCRIPT not found/executable" >&2; exit 1; }

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
