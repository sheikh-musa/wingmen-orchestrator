#!/usr/bin/env bash
# launchd wrapper for sentry_new_error_watch.py (cc-fleet-health, Musa op#19859).
# Sources .env so SENTRY_AUTH_TOKEN/ORG/PROJECT + DATABASE_URL (bus) are present —
# NONE live in the git-tracked plist. The watch itself is INERT until
# SENTRY_WATCH_ENABLED=1 (set in .env at arm-time, after Nazim co-verifies a fire);
# without it the script scans + logs WOULD-* and sends/mutates nothing.
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
exec .venv/bin/python3 scripts/sentry_new_error_watch.py
