#!/usr/bin/env bash
# boot_front_desk.sh — front desk v1 (op#29128, bus #63359/#63381/#63383): the
# "live list" whitelist-answer daemon in front of Nazim's nazim-console DMs.
# Separate process from dev.wingmen.nazim-ingest (same host, same DB — bus
# #63383 item 5) so a hung call here can never dark the operator channel.
#
# FRONT_DESK_ENABLED is the kill switch (item 6) — it is NOT force-exported
# here (orch-console #63427 item 2: an earlier version did `export
# FRONT_DESK_ENABLED=1` unconditionally after sourcing .env, which defeated
# the switch entirely — nothing in .env or a flag file could ever turn it
# off). .env is the ONLY source of truth for this var; set/unset it there.
# front_desk_runner.py re-reads it every drain pass (_tick()), so a change
# takes effect within one DRAIN_INTERVAL even without restarting this
# process — no restart required to flip it off.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.front_desk_runner
