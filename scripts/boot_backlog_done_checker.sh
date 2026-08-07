#!/usr/bin/env bash
# boot_backlog_done_checker.sh — evidence-driven backlog auto-complete loop.
# Flips operator_backlog items to 'done' ONLY when their done_signal's real
# evidence is met (deploy live / PR merged / deploy-stage). Conservative:
# read-only until a signal fires; never auto-completes on an unverifiable check.
# Operator op#9187/9190. Launchd-managed, KeepAlive.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
exec "$ORCH_DIR/.venv/bin/python3" scripts/backlog_done_checker.py
