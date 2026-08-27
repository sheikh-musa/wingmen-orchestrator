#!/usr/bin/env bash
# launchd wrapper for deploy_provenance_watch.py (cc-fleet-health, op#34832).
# Sources .env so VERCEL_TOKEN + GH_TOKEN (gh auth) + DATABASE_URL (bus --alert) are
# present — NONE live in the git-tracked plist. gh reads GH_TOKEN from env, so this
# resolves the gh-auth-under-launchd caveat. Detect-only; --alert posts a deduped bus row.
set -euo pipefail
cd /Users/sheikhmusa/wingmen/orchestrator
set -a
# shellcheck disable=SC1091
source .env
set +a
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
exec .venv/bin/python3 scripts/deploy_provenance_watch.py --alert
