#!/usr/bin/env bash
# launchd wrapper for the idle-with-work watchdog (Musa op#24477/#24479, #48417).
# Sources .env (file-first DSN), then runs the watchdog over the phase-1 lanes every 15 min.
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR" || exit 1
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
unset ANTHROPIC_API_KEY 2>/dev/null || true
exec "$ORCH_DIR/.venv/bin/python3" -m scripts.idle_with_work_watchdog
