#!/bin/bash
# boot_singleton_liveness.sh — launchd wrapper for the protected-singleton death-detection
# monitor (Nazim 35131/35135; closes the cai-killed-30h-undetected gap). DETECT + PAGE only —
# it NEVER auto-boots a singleton (a human relaunches). Sources .env for the bus page.
# Reversible: launchctl bootout the plist.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env 2>/dev/null || true; set +a
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.singleton_liveness "$@"
