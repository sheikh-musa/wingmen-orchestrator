#!/usr/bin/env bash
# launchd wrapper for mini_monitor.py (cc-fleet-health; op#35975, orch-console
# #83733). Sources .env for DATABASE_URL (host_metrics insert + bus page) --
# nothing lives in the git-tracked plist. Same shape as
# run_cubeasht_monitor.sh. Fail-loud: the monitor exits non-zero on any
# DB/paging/state/probe error (exec propagates it to launchd).
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export CC_BASE_AGENT_ID=cc-fleet-health
exec .venv/bin/python3 -u scripts/mini_monitor.py "$@"
