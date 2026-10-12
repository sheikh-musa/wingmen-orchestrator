#!/bin/bash
# Deploy ihsanos.com + irsyad.ihsanos.com to production (wingmen-aa9356e1 team).
# Run this after any merge to main until GitHub auto-deploy is connected to wingmen-aa9356e1.
#
# Usage: scripts/deploy_ihsanos_prod.sh [ihsanos|irsyad|all]
# Default: all
#
# Requires: vercel CLI authenticated with musaaaaaaa account.
set -euo pipefail

SCOPE=wingmen-aa9356e1
REPO_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"
IHSANOS_DIR="$REPO_DIR/../projects/ihsanos"
TARGET="${1:-all}"

if [[ ! -d "$IHSANOS_DIR" ]]; then
  echo "Error: ihsanos repo not found at $IHSANOS_DIR"
  exit 1
fi

# op#29023 / orch-console #63449: FAIL-CLOSED pre-deploy safety. This script deploys the
# WORKING TREE of $IHSANOS_DIR; the guard refuses unless it is clean + on main + == origin/main
# + the linked Vercel project is the intended target. No override flag.
# shellcheck source=scripts/lib/deploy_safety_guard.sh
source "$REPO_DIR/scripts/lib/deploy_safety_guard.sh"

# Expected Vercel ids. Set these to the REAL values (read them from a known-good
# $IHSANOS_DIR/.vercel/project.json after a verified `vercel link`). Fail-closed: the guard's
# project check (d) REFUSES when the expected id is empty — no blind deploy.
VERCEL_ORG_ID="${VERCEL_ORG_ID:-}"
IHSANOS_PROD_PROJECT_ID="${IHSANOS_PROD_PROJECT_ID:-}"
IRSYAD_PROD_PROJECT_ID="${IRSYAD_PROD_PROJECT_ID:-}"

deploy_project() {
  local project=$1
  local label=$2
  local expected_project_id=$3
  echo "=== Deploying $label ($project → $SCOPE) ==="
  cd "$IHSANOS_DIR"
  vercel link --project "$project" --scope "$SCOPE" --yes
  # Guard AFTER link (so .vercel/project.json exists for check (d)) and BEFORE the prod deploy.
  # (a)(b)(c) are tree-state, identical across targets, so the FIRST project's guard aborts the
  # whole run before anything ships if the tree is dirty/behind/off-main — no partial deploy.
  if ! _deploy_safety_guard "$IHSANOS_DIR" "$expected_project_id" "$VERCEL_ORG_ID"; then
    echo "ABORT: deploy-safety guard refused $label — NOT deploying." >&2
    exit 1
  fi
  vercel deploy --prod --scope "$SCOPE"
  echo "=== $label READY ==="
}

if [[ "$TARGET" == "irsyad" || "$TARGET" == "all" ]]; then
  deploy_project "ihsanos-irsyad" "irsyad.ihsanos.com" "$IRSYAD_PROD_PROJECT_ID"
fi

if [[ "$TARGET" == "ihsanos" || "$TARGET" == "all" ]]; then
  deploy_project "ihsanos" "ihsanos.com" "$IHSANOS_PROD_PROJECT_ID"
fi

echo "Done. Production deploys complete."
