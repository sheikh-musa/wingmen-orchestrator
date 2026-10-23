#!/usr/bin/env bash
# boot_media_reaper.sh — run the media reaper PINNED to this host's own
# ingest channels (bus #74154: media bytes land on the ingesting host's
# local disk, so a reaper polling every tag fleet-wide would, the moment a
# second host runs this daemon, download into the wrong host's
# logs/tg_media). Mirrors boot_nazim_ingest.sh's INGEST_CHANNELS pinning —
# MEDIA_REAPER_CHANNELS is the SAME channel_key list as nazim-ingest's own
# INGEST_CHANNELS, since this reaper only ever runs alongside nazim-ingest
# on the Mini. Update both lists together if nazim-ingest's channel set
# ever changes.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
export MEDIA_REAPER_CHANNELS="nazim-console,cosem-exams,cosem-caai,alderei,finance-console,hk-editor,cosem-tdu,angullia,oeh,mamadah,coffeemedia,brandit-coffeemedia,coffeemedia-hamhuskies-test"
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.media_reaper --fire
