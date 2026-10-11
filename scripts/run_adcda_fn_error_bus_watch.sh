#!/usr/bin/env bash
# launchd wrapper for adcda_fn_error_bus_watch.py (cc-fleet-health, op#28966).
# Sources .env so DATABASE_URL (bus send) + optional GCP_SA_KEY / FLEET_FN_ERROR_WATCH_ENABLED
# are present — NONE live in the git-tracked plist. The watch is INERT until
# FLEET_FN_ERROR_WATCH_ENABLED=1 (set in .env at arm-time); without it the script polls,
# logs WOULD-FIRE, and sends/mutates nothing. gcloud (for the cosem-sa token) resolves via
# /usr/local/bin on the exported PATH. /bin/bash is 3.2 — no bash-4 constructs here.
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
exec .venv/bin/python3 scripts/adcda_fn_error_bus_watch.py
