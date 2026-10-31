#!/usr/bin/env bash
# op#36531/36535 TEMPORARY canary supervisor boot wrapper. Not a permanent fleet
# daemon -- scoped to the angullia headless-canary, remove with the plist once
# the canary ends (bus #86360 condition 4/5; moved into the tracked live
# checkout per bus #87654/#87739).
set -euo pipefail
ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/headless_canary_angullia.py"
