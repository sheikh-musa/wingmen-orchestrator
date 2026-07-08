#!/usr/bin/env bash
# break_glass.sh — EMERGENCY restart of the operator's MacBook fleet nodes
# (Nazim console + cai), then drop into the Nazim console to talk IN-CONSOLE.
#
# Normal ops: talk to Nazim on Telegram at @nazim_cto_bot (always-on Mac Mini).
# This is the "break glass" for when you need Nazim/cai HERE on the MacBook —
# e.g. the Mini/Telegram is unreachable. A fresh Nazim re-orients itself from
# STATUS.md + memory + the operator log, so it boots caught-up.
# Created 2026-07-08 (Nazim->Mini cutover).
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
CAI_DIR="$HOME/wingmen/wingmen-cai"

TMUX_BIN="$(command -v tmux || true)"
if [[ -z "$TMUX_BIN" ]]; then
  for t in /opt/homebrew/bin/tmux /usr/local/bin/tmux; do [[ -x "$t" ]] && TMUX_BIN="$t" && break; done
fi
[[ -n "$TMUX_BIN" ]] || { echo "tmux not found — cannot break glass"; read -r -p "Enter to close..."; exit 1; }

echo "==============================================="
echo "  BREAK GLASS — restarting MacBook fleet nodes"
echo "==============================================="
echo ""

start_session() {   # name  dir  boot_script
  local name="$1" dir="$2" boot="$3"
  if "$TMUX_BIN" has-session -t "$name" 2>/dev/null; then
    echo "  [ok]    $name — already running"
  elif [[ -x "$boot" ]]; then
    echo "  [start] $name — booting..."
    "$TMUX_BIN" new-session -d -s "$name" -x 220 -y 50 -c "$dir" "bash -lc 'exec $boot'"
  else
    echo "  [skip]  $name — boot script not found ($boot)"
  fi
}

start_session nazim "$ORCH_DIR" "$ORCH_DIR/scripts/boot_nazim.sh"
start_session cai   "$CAI_DIR"   "$CAI_DIR/boot_cai.sh"

echo ""
echo "  Nazim (Opus 4.8, console body) is booting — re-orients from STATUS + memory + operator log."
echo "  Detach without killing:  Ctrl-b  then  d"
echo ""
sleep 3

if [[ -n "${TMUX:-}" ]]; then
  exec "$TMUX_BIN" switch-client -t nazim
else
  exec "$TMUX_BIN" attach -t nazim
fi
