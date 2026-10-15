#!/usr/bin/env bash
# launchd wrapper for glm_peak_pause_resume.py --apply (cc-fleet-health; Musa
# op#30538/op#30543/op#30563, orch-console #67477/#67479/#67504/#67507). Every ~10 min:
# at GLM peak (06:00-10:00Z), any currently-GLM lane with fleet_lanes.glm_peak_urgent=true
# flips to the Musa/Anthropic pool and keeps working, the rest pause (0 pool burn); at
# off-peak, reverses exactly what this run's state recorded. Periodic-check-vs-window, not
# a brittle exact-minute timer -- a missed single firing is caught on the next one. Sources
# .env for DATABASE_URL; the Musa/Syed tokens come from ~/.wingmen/keys, never this file.
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export CC_BASE_AGENT_ID=cc-fleet-health
exec .venv/bin/python3 -u scripts/glm_peak_pause_resume.py --apply "$@"
