#!/usr/bin/env bash
# launchd wrapper for cubeasht_monitor.py (cc-fleet-health; orch-console #58069/#58243,
# Musa op#27169). Sources .env so GH_TOKEN (gh's stored keyring token is NOT reliable
# under launchd) + DATABASE_URL (host_metrics insert + bus page) are present — NONE live
# in the git-tracked plist. Same shape as run_ci_queue_watchdog.sh. Fail-loud: the
# monitor exits non-zero on any DB/paging/state/gh/probe-parse error (exec propagates
# it to launchd). A desktop that does not answer ssh is DATA (ok=false row), not an error.
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export CC_BASE_AGENT_ID=cc-fleet-health
exec .venv/bin/python3 -u scripts/cubeasht_monitor.py "$@"
