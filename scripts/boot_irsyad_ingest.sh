#!/usr/bin/env bash
# boot_irsyad_ingest.sh — DEDICATED unified ingest daemon PINNED to the irsyad
# client channel (INGEST_CHANNELS=gazzabyte-irsyad): the inbound poller for
# @irsyad_support_bot (Gazzabyte client group), routing to the irsyad-coord body.
#
# Deliberately ISOLATED from nazim-ingest (operator/console bot) per hub decision
# op#13-#22090 (#22017): a restart or bot issue on either daemon must never affect
# the other. Co-located on the Mini WITH the irsyad-coord tmux session so
# nudge_session() (local tmux send-keys) actually reaches the coord — the whole
# point of the re-point. gazzabyte-irsyad stays enabled=false in bot_channels so
# the hub's all-enabled ingest never touches this bot token (no dual-poller 409);
# INGEST_CHANNELS pins it here REGARDLESS of the enabled flag. Launchd, KeepAlive.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
export INGEST_CHANNELS="gazzabyte-irsyad"
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.ingest
