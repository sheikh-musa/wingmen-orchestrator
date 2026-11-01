#!/usr/bin/env bash
# launchd wrapper for the read-but-unanswered digest (orch-console #88653 / ruling #88660).
#
# Report-only. It probes each live lane's tmux pane (lane_is_drained) and posts ONE P3
# digest of lanes that READ a requires_response row, never answered it, and are idle.
# It never nudges and never mutates a lane — console explicitly ruled out a fleet-wide
# nudge net (#88660).
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR" || exit 1
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
unset ANTHROPIC_API_KEY 2>/dev/null || true
exec "$ORCH_DIR/.venv/bin/python3" -m scripts.read_unanswered_digest --post "$@"
