#!/usr/bin/env bash
# boot_nazim_bus_notify.sh — realtime poke for the Nazim console when a NEW fleet
# bus row (agent_messages -> orch-console) lands. Closes the async-substrate
# latency (orch's questions no longer sit until Nazim's next reconcile) WITHOUT a
# new channel: a count-only nudge into the `nazim` tmux session. The substrate
# stays the durable source of truth; this is signal, not delivery. Launchd-managed.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
export ORCH_TMUX_SESSION="${ORCH_TMUX_SESSION:-nazim}"
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.nazim_bus_notify
