#!/usr/bin/env bash
# nudge_support.sh — the ONLY sanctioned programmatic injection into the cc-support
# tmux session (op#4537). NUDGE-ONLY with a provenance header (same discipline as
# nudge_cai.sh / R1 CAI-RESP-377): never content, never client/operator words, never
# authorization claims. cc-support reads the actual client messages itself from the
# durable log (operator_messages, channel gazzabyte-irsyad) and drafts under Phase-A
# supervision. This nudge only tells it HOW MANY unhandled client messages await.
#
# The hub calls this after a new gazzabyte-irsyad inbound so cc-support wakes and
# drafts (Phase A: it posts a support_draft; hub/Nazim approve+send, op#4559 SLA).
#
# Usage: scripts/nudge_support.sh          # auto: count unhandled gazzabyte-irsyad inbound
#        scripts/nudge_support.sh <N>      # explicit numeric count (no free text)
set -euo pipefail
ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMUX_BIN="$(command -v tmux || echo /opt/homebrew/bin/tmux)"

N="${1:-}"
if [ -z "$N" ]; then
  set -a; source "$ORCH_DIR/.env"; set +a
  N="$(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - <<'PY'
import os, psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
    cur.execute("""SELECT count(*) FROM operator_messages
                   WHERE tag='gazzabyte-irsyad' AND direction='inbound' AND handled_at IS NULL""")
    print(cur.fetchone()[0])
PY
)"
fi
case "$N" in (*[!0-9]*|'') echo "nudge_support: count must be numeric — free text is forbidden" >&2; exit 2;; esac

LINE="[cc-orchestrator relay] ${N} unhandled on 'gazzabyte-irsyad' — reconcile operator_log + draft (Phase A)"
"$TMUX_BIN" has-session -t '=support' 2>/dev/null || { echo "nudge_support: no live support session (log is durable; nothing lost)" >&2; exit 0; }
"$TMUX_BIN" send-keys -t '=support:0.0' -l "$LINE"
sleep 1
"$TMUX_BIN" send-keys -t '=support:0.0' Enter
echo "nudged support: $LINE"
