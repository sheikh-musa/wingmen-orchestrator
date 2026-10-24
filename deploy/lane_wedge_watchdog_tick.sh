#!/bin/bash
# lane_wedge_watchdog_tick.sh — one gzb-scoped lane-wedge scan, ALERT-ONLY (op#39439/#74995,
# orch-console gate 2026-10-24). Phase 1 of the phased-arm plan in
# docs/coord-self-keepalive-design-v1.md: detect + page only, ZERO recover actions. No --arm
# flag is ever passed by this tick — escalating to --arm=nudge or --arm needs a fresh,
# separate orch-console gate decision per that doc; do not add it here without one.
# No-root/no-pw (user cron), same pattern as autoscaler_tick.sh — each fire is independent,
# host-scoped automatically via orch_lease._me() (gzb lanes only; Mini lanes stay the SRE's).
# Runs under the HUB's own lane-lifecycle pen (#40072) — not fleet_health_lease, since this is
# detect+page only and touches no agent.
set -uo pipefail
cd /home/gazzai/wingmen/orchestrator || exit 1
set -a; . /dev/shm/wingmen-secrets/.env 2>/dev/null; set +a
exec /home/gazzai/wingmen/orchestrator/.venv/bin/python3 nervous_system/lane_wedge_watchdog.py --once --alert
