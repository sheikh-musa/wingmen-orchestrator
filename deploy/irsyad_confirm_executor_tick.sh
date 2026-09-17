#!/bin/bash
# irsyad_confirm_executor_tick.sh — one confirm-executor pass for the cron (Nazim #40639).
# Makes a standing 'CONFIRM SPIN <id>' always execute (or alert loudly) WITHOUT depending on the
# hub being awake — closes the CAI-451-wake-floor gap where an rr=false confirm sat unexecuted.
# Safe: reuses the autoscaler's own would_spin decision, so it never over-spins a met demand;
# the actuator it calls is itself confirm-gated + MAX_LANES-capped.
set -uo pipefail
cd /home/gazzai/wingmen/orchestrator || exit 1
set -a; . /dev/shm/wingmen-secrets/.env 2>/dev/null; set +a
exec /home/gazzai/wingmen/orchestrator/.venv/bin/python3 scripts/irsyad_confirm_executor.py --once
