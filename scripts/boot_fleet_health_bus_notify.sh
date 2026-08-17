#!/usr/bin/env bash
# boot_fleet_health_bus_notify.sh — realtime poke for the cc-fleet-health SRE lane
# on NEW fleet bus rows. Peer of boot_nazim/cai/finance_bus_notify. Fires the
# sanctioned verified-submit lane nudge (lane_nudge.sh, ghost-aware) the moment a
# bus row -> cc-fleet-health lands, so the SRE drains in realtime instead of on its
# slower heartbeat. Closes the operator-flagged gap (cc-fleet-health was the only
# coordinator without a realtime bus-notifier). Launchd-managed, KeepAlive.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.fleet_health_bus_notify
