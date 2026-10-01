#!/usr/bin/env bash
# Wrapper for the TEMPORARY op#24342 pooler auth-fail tripwire launchd job
# (dev.wingmen.pooler-authfail-tripwire). Sources .env for DATABASE_URL +
# SUPABASE_ACCESS_TOKEN, then runs the tripwire. REMOVE this + the plist when the
# category-(c) env-first-_dsn PR is merged + pulled (orch-console #48335).
#
# MULTI-HOST: the tripwire reads BOTH Mini and gzb auth-fails via the Supabase Mgmt
# API (read-only HTTPS), so it only needs to run on ONE host (the Mini). Do NOT also
# schedule a second copy on gzb — one Mgmt-API-backed tripwire already covers the fleet.
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR" || exit 1
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
unset ANTHROPIC_API_KEY 2>/dev/null || true
exec "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/pooler_authfail_tripwire.py"
