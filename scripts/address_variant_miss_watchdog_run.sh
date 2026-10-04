#!/usr/bin/env bash
# launchd wrapper for the address-variant-miss watchdog (bus #51166/#51195).
# Sources .env (file-first DSN), runs the detector every 15 min. ARMED
# (--page, orch-console #51240 GO) -- pages orch-console on a genuine miss,
# deduped to one page per stuck lane per day (AVM_PAGE_COOLDOWN_MIN=1440).
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR" || exit 1
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
unset ANTHROPIC_API_KEY 2>/dev/null || true
export AVM_PAGE_COOLDOWN_MIN=1440
exec "$ORCH_DIR/.venv/bin/python3" -m scripts.address_variant_miss_watchdog --page
