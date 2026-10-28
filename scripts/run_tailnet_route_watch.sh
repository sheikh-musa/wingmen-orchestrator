#!/usr/bin/env bash
# launchd wrapper for tailnet_route_watch.py (cc-fleet-health; orch-console #79806).
# Sources .env for DATABASE_URL (bus page) — no secrets live in the git-tracked plist.
# Same shape as run_cubeasht_monitor.sh. Fail-loud: the monitor exits non-zero on any
# state/paging error (exec propagates it to launchd). A missing route is DATA (paged), not an error.
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export CC_BASE_AGENT_ID=cc-fleet-health
exec .venv/bin/python3 -u scripts/tailnet_route_watch.py "$@"
