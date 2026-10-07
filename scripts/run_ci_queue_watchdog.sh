#!/usr/bin/env bash
# launchd wrapper for ci_queue_watchdog.py (cc-fleet-health, orch-console bus #57952).
# Sources .env so GH_TOKEN (gh auth — the host's stored gh keyring token is NOT
# reliable under launchd) + DATABASE_URL (bus page) are present — NONE live in the
# git-tracked plist. Same shape as run_deploy_provenance_watch.sh. Fail-loud: the
# watchdog exits non-zero on any gh/parse/paging error (set -e propagates it).
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
exec .venv/bin/python3 scripts/ci_queue_watchdog.py "$@"
