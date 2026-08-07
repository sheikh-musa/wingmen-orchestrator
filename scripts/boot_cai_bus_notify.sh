#!/usr/bin/env bash
# boot_cai_bus_notify.sh — realtime poke for the cai governance node on NEW fleet
# bus rows. Peer of boot_nazim_bus_notify.sh. Fires cai's SANCTIONED count-only
# nudge (nudge_cai.sh, ghost-aware) the moment a bus row -> cai lands, so cai
# drains in realtime instead of waiting on the reactive wedge-watchdog. The
# asymmetry this closes stalled the mig134 money-path grant ~6h overnight
# (operator 2026-08-02). Launchd-managed, KeepAlive.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.cai_bus_notify
