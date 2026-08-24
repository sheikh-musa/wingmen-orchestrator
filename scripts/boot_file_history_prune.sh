#!/bin/bash
# boot_file_history_prune.sh — launchd wrapper for the file-history prune-monitor
# (disk-wedge recurrence safety-net, 2026-08-24). Sources .env for the alert bus-post,
# then runs the prune in --apply mode. Reversible: launchctl bootout the plist.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
exec "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/file_history_prune.py" --apply
