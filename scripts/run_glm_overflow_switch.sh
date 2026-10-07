#!/usr/bin/env bash
# launchd wrapper for glm_overflow_switch.py --apply (cc-fleet-health; Musa op#27156,
# orch-console #58028/#58036). Every 5 min: if GLM weekly >=95% (or 5h >=95% with weekly
# >=90%) and Musa has headroom, move every GLM lane onto Musa's token; retries PENDING
# lanes; never switches back. Sources .env for DATABASE_URL (fleet_lanes stamp + bus page);
# the GLM key comes from the vault inside glm_usage, never from this file. tmux uses the
# default user socket (/private/tmp/tmux-<uid>/default), the same server the lanes run on.
# Fail-loud: the script exits 2 on an unreadable GLM/Musa reading (set -e propagates it)
# and 1 when a lane is still PENDING, so launchd's last-exit shows both.
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export CC_BASE_AGENT_ID=cc-fleet-health
exec .venv/bin/python3 -u scripts/glm_overflow_switch.py --apply "$@"
