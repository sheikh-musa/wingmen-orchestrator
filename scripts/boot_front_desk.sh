#!/usr/bin/env bash
# boot_front_desk.sh — front desk v1 (op#29128, bus #63359/#63381/#63383): the
# instant-ack + "live list" whitelist-answer daemon in front of Nazim's
# nazim-console DMs. Separate process from dev.wingmen.nazim-ingest (same
# host, same DB — bus #63383 item 5) so a hung call here can never dark the
# operator channel. FRONT_DESK_ENABLED=1 is the kill switch (item 6) — unset
# or any other value and main() no-ops without touching the DB, so ingest.py/
# Nazim's own reconciliation is unaffected either way. Launchd-managed,
# KeepAlive.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
export FRONT_DESK_ENABLED=1
exec "$ORCH_DIR/.venv/bin/python3" -m nervous_system.front_desk_runner
