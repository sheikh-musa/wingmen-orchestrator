#!/bin/bash
# autoscaler_tick.sh — one irsyad-autoscaler tick, for the cron soak (Nazim 39345 ruling-3).
# No-root/no-pw soak (user cron), restart-safe + self-healing (each fire independent — NOT a loop),
# meets the "timer not a loop" bar without needing the (scrubbed, pending-rotation) gzb sudo pw.
# FULLY-AUTO (Nazim #40850 (3), granted #40833/#40844/#40847): on pool-safe would_spin the
# autoscaler ACTUATES directly (irsyad_spin_worker --auto), no orch-console confirm. Guards still
# enforced: demand-gate (re-eval would_spin before each boot), MAX_LANES=2, cap=spun workers only
# (standing lanes excluded), one-boot idempotency. Worker PRs still go to Nazim's gate; money
# go-live stays behind the money gate. Reaper (*/5) shrinks the pool on wind-down so this re-scales.
set -uo pipefail
cd /home/gazzai/wingmen/orchestrator || exit 1
set -a; . /dev/shm/wingmen-secrets/.env 2>/dev/null; set +a
exec /home/gazzai/wingmen/orchestrator/.venv/bin/python3 scripts/irsyad_autoscaler.py --once --mode auto
