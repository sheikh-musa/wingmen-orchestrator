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

deploy_project() {
  local project=$1
  local label=$2
  echo "=== Deploying $label ($project → $SCOPE) ==="
  cd "$IHSANOS_DIR"
  vercel link --project "$project" --scope "$SCOPE" --yes
  vercel deploy --prod --scope "$SCOPE"
  echo "=== $label READY ==="
}

if [[ "$TARGET" == "irsyad" || "$TARGET" == "all" ]]; then
  deploy_project "ihsanos-irsyad" "irsyad.ihsanos.com"
fi

if [[ "$TARGET" == "ihsanos" || "$TARGET" == "all" ]]; then
  deploy_project "ihsanos" "ihsanos.com"
fi

echo "Done. Production deploys complete."
