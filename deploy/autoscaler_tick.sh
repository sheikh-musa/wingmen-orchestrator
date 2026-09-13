#!/bin/bash
# autoscaler_tick.sh — one INERT irsyad-autoscaler tick, for the cron soak (Nazim 39345 ruling-3).
# No-root/no-pw soak (user cron), restart-safe + self-healing (each fire independent — NOT a loop),
# meets the "timer not a loop" bar without needing the (scrubbed, pending-rotation) gzb sudo pw.
# INERT: writes one row to fleet_lane_autoscale_log; ZERO actuation. Remove after wet-prove@Nazim-gate.
set -uo pipefail
cd /home/gazzai/wingmen/orchestrator || exit 1
set -a; . /dev/shm/wingmen-secrets/.env 2>/dev/null; set +a
exec /home/gazzai/wingmen/orchestrator/.venv/bin/python3 scripts/irsyad_autoscaler.py --once
