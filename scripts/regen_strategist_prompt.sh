#!/bin/bash
# Regenerate cto_strategist_prompt.ts from cto_strategist_prompt.md.
#
# The .md is the human-editable source of truth for Al-Mushtashir's system
# prompt. The .ts is the bundled artifact that gets shipped with the edge
# function, because supabase's --use-api bundler follows TS import graphs
# but does NOT include arbitrary .md file assets.
#
# Run this after every edit to cto_strategist_prompt.md, BEFORE deploying.
# The deploy script at scripts/deploy_strategist.sh wraps this step
# automatically; use that instead of calling this directly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FN_DIR="$SCRIPT_DIR/../supabase/functions/cto-strategist"
cd "$FN_DIR"

if [[ ! -f cto_strategist_prompt.md ]]; then
  echo "ERROR: cto_strategist_prompt.md not found in $FN_DIR" >&2
  exit 1
fi

python3 <<'PYEOF'
import json
with open('cto_strategist_prompt.md') as f:
    md = f.read()
header = "// GENERATED FROM cto_strategist_prompt.md -- do not edit by hand.\n// Regenerate: scripts/regen_strategist_prompt.sh\n\n"
body = "export const STRATEGIST_PROMPT = " + json.dumps(md) + ";\n"
with open('cto_strategist_prompt.ts', 'w') as f:
    f.write(header + body)
print(f"wrote cto_strategist_prompt.ts from {len(md)} bytes of markdown")
PYEOF
