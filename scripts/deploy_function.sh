#!/bin/bash
# Generic deploy gate for supabase edge functions.
#
# Wraps `supabase functions deploy` and refuses to exit 0 unless a
# post-deploy `?mode=full-self-test` probe returns {"ok": true}. Every
# edge function in this repo must implement full-self-test OR be listed
# in scripts/deploy_function.config.json as opted-out.
#
# Origin: council session 1 (2026-04-11), ended=consensus.
# Default is opt-out-by-allowlist: enforced discipline beats remembered
# discipline.
#
# Usage:
#   ./scripts/deploy_function.sh <function-name> [additional supabase flags]
#
# Example:
#   ./scripts/deploy_function.sh cto-strategist
#
# Requires: SUPABASE_ACCESS_TOKEN, SUPABASE_PROJECT_REF, SUPABASE_SERVICE_KEY
# in .env or environment. jq, python3, curl, supabase CLI on PATH.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <function-name> [supabase flags...]" >&2
  exit 2
fi

FN_NAME="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH_DIR="$SCRIPT_DIR/.."
CONFIG_FILE="$SCRIPT_DIR/deploy_function.config.json"
cd "$ORCH_DIR"

# shellcheck source=/dev/null
set -a; source .env; set +a

: "${SUPABASE_ACCESS_TOKEN:?SUPABASE_ACCESS_TOKEN must be set}"
: "${SUPABASE_PROJECT_REF:?SUPABASE_PROJECT_REF must be set}"
: "${SUPABASE_SERVICE_KEY:?SUPABASE_SERVICE_KEY must be set}"

# Check the opt-out allowlist
if [[ -f "$CONFIG_FILE" ]]; then
  GATE_MODE=$(python3 -c "
import json, sys
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
optouts = cfg.get('opt_out', {})
fn = '$FN_NAME'
if fn in optouts:
    print('health')
    print(f\"  reason: {optouts[fn].get('reason', 'unspecified')}\", file=sys.stderr)
    print(f\"  since: {optouts[fn].get('since', 'unknown')}\", file=sys.stderr)
else:
    print('full-self-test')
")
else
  GATE_MODE="full-self-test"
fi

echo "==> Deploying $FN_NAME (gate mode: $GATE_MODE)"
supabase functions deploy "$FN_NAME" --project-ref "$SUPABASE_PROJECT_REF" "$@"

echo "==> Gate: probing ?mode=$GATE_MODE"
GATE_URL="https://${SUPABASE_PROJECT_REF}.supabase.co/functions/v1/${FN_NAME}?mode=${GATE_MODE}"

# Poll for up to 30s — the function may take a moment to reach a cold-start
# pool on the first probe after deploy.
DEADLINE=$(( $(date +%s) + 30 ))
GATE_BODY=""
GATE_STATUS=""
while [[ $(date +%s) -lt $DEADLINE ]]; do
  RESP=$(curl -sS -o /tmp/gate_body.$$ -w "%{http_code}" \
    "$GATE_URL" -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY}" || true)
  GATE_STATUS="$RESP"
  GATE_BODY=$(cat /tmp/gate_body.$$ 2>/dev/null || echo "")
  rm -f /tmp/gate_body.$$
  if [[ "$GATE_STATUS" == "200" ]]; then
    break
  fi
  sleep 2
done

echo "$GATE_BODY"

python3 - <<PYEOF
import json, sys
body = '''$GATE_BODY'''
status = '$GATE_STATUS'
if status != '200':
    print(f"==> FAIL: HTTP {status}", file=sys.stderr)
    sys.exit(1)
try:
    h = json.loads(body)
except Exception as e:
    print(f"==> FAIL: gate response not JSON: {e}", file=sys.stderr)
    sys.exit(1)
if not h.get('ok'):
    failed_at = h.get('failed_at', '?')
    err = h.get('error', '?')
    print(f"==> FAIL: gate returned ok=false, failed_at={failed_at}, error={err}", file=sys.stderr)
    sys.exit(1)
print(f"==> PASS: $FN_NAME healthy")
if '$GATE_MODE' == 'full-self-test':
    assertions = h.get('assertions_passed', [])
    print(f"    assertions: {', '.join(assertions)}")
    usage = h.get('probe_usage') or {}
    if usage:
        print(f"    probe usage: in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}")
PYEOF
