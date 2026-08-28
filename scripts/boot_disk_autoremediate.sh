#!/bin/bash
# boot_disk_autoremediate.sh — launchd wrapper for the tiered disk auto-remediation
# (pre-crash reclaim safety-net, 2026-08-28; §4.6). Sources .env for the fail-loud bus-page,
# then runs disk_autoremediate.py with whatever args the plist passes.
#
# ARMING: the plist ships with NO --apply (DRY-RUN — reports what it WOULD free, touches
# nothing). To ARM after operator/Nazim reviews the dry-run: add "--apply" to the plist's
# ProgramArguments and re-bootstrap. Reversible: launchctl bootout the plist.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
exec "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/disk_autoremediate.py" "$@"
