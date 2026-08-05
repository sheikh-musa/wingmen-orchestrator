#!/usr/bin/env bash
# boot_agent_wake_subscriber.sh — launchd wrapper for the Mini realtime AUTO-WAKE
# subscriber (Gap B of the auto-nudge, CAI-RESP-706). Wake-only; wakes MINI lanes
# the instant a directed agent_messages row lands. Runs in orch-console's (Nazim's)
# launchd domain. Peer of boot_weekly_alert_relay.sh. KeepAlive-managed.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
# Source .env so AUTO_WAKE_ENABLED, SUPABASE_URL/SERVICE_KEY, DATABASE_URL are set.
set -a; . ./.env; set +a
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.agent_wake_subscriber
