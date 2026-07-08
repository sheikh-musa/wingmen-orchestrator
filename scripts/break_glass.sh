#!/usr/bin/env bash
# break_glass.sh — EMERGENCY restart of the operator's MacBook Nazim console,
# then drop into it to talk IN-CONSOLE.
#
# Normal ops: talk to Nazim on Telegram at @nazim_cto_bot (always-on Mac Mini).
# This is the "break glass" for when you need Nazim HERE on the MacBook — e.g.
# the Mini/Telegram is unreachable. A fresh Nazim re-orients from STATUS.md +
# memory + the operator log, so it boots caught-up.
#
# NOTE: cai is NOT restarted here — it lives on the Mac Studio, spun up on-demand
# for governance work. The MacBook only hosts the Nazim emergency console.
# Created 2026-07-08 (Nazim->Mini cutover).
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"

TMUX_BIN="$(command -v tmux || true)"
if [[ -z "$TMUX_BIN" ]]; then
  for t in /opt/homebrew/bin/tmux /usr/local/bin/tmux; do [[ -x "$t" ]] && TMUX_BIN="$t" && break; done
fi
[[ -n "$TMUX_BIN" ]] || { echo "tmux not found — cannot break glass"; read -r -p "Enter to close..."; exit 1; }

echo "==============================================="
echo "  BREAK GLASS — restarting the Nazim console"
echo "==============================================="
echo ""

if "$TMUX_BIN" has-session -t nazim 2>/dev/null; then
  echo "  [ok]    nazim — already running"
elif [[ -x "$ORCH_DIR/scripts/boot_nazim.sh" ]]; then
  echo "  [start] nazim — booting Opus 4.8 console body..."
  "$TMUX_BIN" new-session -d -s nazim -x 220 -y 50 -c "$ORCH_DIR" "bash -lc 'exec $ORCH_DIR/scripts/boot_nazim.sh'"
else
  echo "  [fail]  boot_nazim.sh not found under $ORCH_DIR/scripts/ — is the repo present?"
  read -r -p "Enter to close..."; exit 1
fi

echo ""
echo "  Nazim re-orients from STATUS + memory + the operator log, so it boots caught-up."
echo "  Detach without killing:  Ctrl-b  then  d"
echo ""
sleep 3

if [[ -n "${TMUX:-}" ]]; then
  exec "$TMUX_BIN" switch-client -t nazim
else
  exec "$TMUX_BIN" attach -t nazim
fi
