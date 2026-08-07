#!/usr/bin/env bash
# boot_weekly_alert_relay.sh — DRAFTED by cc-fleet-health (the SRE) for orch-console
# (Nazim), at Nazim's request (#15059). Peer of boot_cai_bus_notify.sh.
#
# Delivers the SRE's operator-addressed weekly-limit ⚠️ warnings (bus rows
# to_agent='musa', from_agent='cc-fleet-health') to the operator's phone via
# NAZIM'S OWN pen — scripts/nazim_send.sh / @nazim_cto_bot — NOT the hub's pen iv.
# It therefore runs as NAZIM's launchd job (he owns, reviews, and runs it); the SRE
# only drafted the code and never runs it, staying inside its own pen boundary.
# Launchd-managed, KeepAlive.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.weekly_alert_relay
