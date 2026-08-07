#!/usr/bin/env bash
# boot_finance_bus_notify.sh — realtime poke for the cc-finance lane on NEW fleet
# bus rows. Peer of boot_nazim/cai_bus_notify. Fires the sanctioned verified-submit
# lane nudge (lane_nudge.sh, ghost-aware) the moment a bus row -> cc-finance lands.
# Operator op#9175. Launchd-managed, KeepAlive.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.finance_bus_notify
