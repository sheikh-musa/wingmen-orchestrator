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
# lane_wedge_watchdog.py's own docstring says "the scheduled --alert run sets
# [LANE_WEDGE_PERSIST_DRY=1]" but this script never did -- a detect-only (--alert,
# no --arm) run's `persist` flag stayed False, so save_state() never ran and the
# once-per-episode COVERAGE-GAP dedup (coverage_gap_episode) saw an empty `warned`
# set EVERY tick, re-paging orch-console every 7 minutes for any unmapped session
# instead of once per episode (orch-console bus #76401, 2026-10-25).
export LANE_WEDGE_PERSIST_DRY=1
exec /home/gazzai/wingmen/orchestrator/.venv/bin/python3 nervous_system/lane_wedge_watchdog.py --once --alert
