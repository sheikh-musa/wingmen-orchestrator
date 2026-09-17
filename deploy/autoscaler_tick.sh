#!/bin/bash
# autoscaler_tick.sh — one irsyad-autoscaler tick, for the cron soak (Nazim 39345 ruling-3).
# No-root/no-pw soak (user cron), restart-safe + self-healing (each fire independent — NOT a loop),
# meets the "timer not a loop" bar without needing the (scrubbed, pending-rotation) gzb sudo pw.
# ARMED SUPERVISED (Nazim #40401): writes one row to fleet_lane_autoscale_log AND, when demand
# appears (coord_dispatch_queue), POSTS a deduped propose-then-confirm bus row to orch-console.
# It NEVER auto-spins/kills — spin execution stays a Nazim-confirm-gated, wet-proved step.
# Dormant until the coord queue exists (demand=0 => no proposal). IRSYAD_AUTOSCALER_MODE overrides.
set -uo pipefail
cd /home/gazzai/wingmen/orchestrator || exit 1
set -a; . /dev/shm/wingmen-secrets/.env 2>/dev/null; set +a
exec /home/gazzai/wingmen/orchestrator/.venv/bin/python3 scripts/irsyad_autoscaler.py --once --mode supervised
