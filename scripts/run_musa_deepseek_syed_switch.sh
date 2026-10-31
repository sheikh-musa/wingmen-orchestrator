#!/usr/bin/env bash
# launchd wrapper for musa_deepseek_syed_switch.py --apply (cc-fleet-health; Musa
# op#36560/36562, orch-console #86307/#86319). Every 5 min: Musa 7d >=90% -> move every
# worker lane currently on Musa's Claude token onto DeepSeek (provider_policy-gated,
# skip-and-report for any uncleared lane); Musa 7d >=95% -> ALSO move the brains
# (cc-fleet-health, orch-console, cc-quality) + mamadah onto Syed via
# switch_singleton_token.sh / switch_lane_token.sh. Never switches back. Sources .env
# for DATABASE_URL (fleet_lanes/agent_status reads + bus page).
# Fail-loud: the script exits 2 on an unreadable Musa reading (set -e propagates it)
# and 1 when a lane/node is still PENDING/FAILED, so launchd's last-exit shows both.
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export CC_BASE_AGENT_ID=cc-fleet-health
exec .venv/bin/python3 -u scripts/musa_deepseek_syed_switch.py --apply "$@"
