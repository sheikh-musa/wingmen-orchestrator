#!/usr/bin/env bash
# boot_nazim_ingest.sh — run the unified ingest daemon PINNED to Nazim's own
# Telegram channel (INGEST_CHANNELS=nazim-console): the console body's inbound
# poller for @nazim_cto_bot. Host-isolated from the hub's ingest, which polls
# every `enabled` channel — nazim-console stays enabled=false so the hub never
# touches this bot token (no dual-poller 409). Launchd-managed, KeepAlive.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
export INGEST_CHANNELS="nazim-console,cosem-exams,cosem-caai,alderei,finance-console,hk-editor,cosem-tdu,angullia,oeh,mamadah"
# wingmen-personal credential for the mamadah personal-routing split-write
# (bus #47837 C3) — GUARDED file, deliberately NOT in .env (that's sourced
# with `set -a`, which would export it to every fleet lane via
# launch_dangerous_cc.sh). Scoped to this ingest process only.
export WINGMEN_PERSONAL_ALLOWED=1
. "$HOME/.wingmen/private/wingmen_personal.env"
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.ingest
