#!/bin/bash
# Deploy the cto-strategist edge function.
#
# Regenerates the prompt .ts artifact from the .md source, then delegates
# to the generic deploy gate (scripts/deploy_function.sh), which runs the
# full-self-test probe post-deploy.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Regenerating cto_strategist_prompt.ts"
"$SCRIPT_DIR/regen_strategist_prompt.sh"

echo "==> Delegating to deploy_function.sh"
exec "$SCRIPT_DIR/deploy_function.sh" cto-strategist
