#!/usr/bin/env bash
# boot_gzb_media_reaper.sh — run the media reaper PINNED to the gzb hub's own
# ingest channels (bus #74154: media bytes land on the ingesting host's local
# disk, so a reaper polling every tag fleet-wide would, the moment a second
# host runs this daemon, download into the wrong host's logs/tg_media).
# Mirrors scripts/boot_media_reaper.sh's channel-pinning convention (Mini),
# but gzb reads secrets from the tmpfs bundle, not a repo ./.env, and the
# hub's ingest.py polls every bot_channels row with enabled=true rather than
# a fixed INGEST_CHANNELS list -- so MEDIA_REAPER_CHANNELS here is pinned
# directly to the gzb hub's current enabled set (confirmed via bot_channels
# query, op#33149/commitment #547): cai-channel, gazzabyte-irsyad,
# operator-orch. Update this list if the hub's enabled channel set changes.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . /dev/shm/wingmen-secrets/.env; set +a
export MEDIA_REAPER_CHANNELS="cai-channel,gazzabyte-irsyad,operator-orch"
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.media_reaper --fire
