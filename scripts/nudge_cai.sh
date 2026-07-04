#!/usr/bin/env bash
# nudge_cai.sh — R1 (CAI-RESP-377): the ONLY sanctioned programmatic injection
# into the cc-cai tmux session. NUDGE-ONLY with a provenance header — never
# content, never operator words, never authorization claims. Anything cai needs
# to read arrives as an attributable substrate row (agent_messages), judged as
# agent testimony; operator words reach cai only via bridge-logged rows cited by
# operator_messages id, cockpit-verified rows, or his own typing at cai's console.
#
# Usage: scripts/nudge_cai.sh            # auto: count cai's unread bus messages
#        scripts/nudge_cai.sh <N>        # explicit count (no free text accepted)
set -euo pipefail
ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMUX_BIN="$(command -v tmux || echo /opt/homebrew/bin/tmux)"

N="${1:-}"
if [ -z "$N" ]; then
  set -a; source "$ORCH_DIR/.env"; set +a
  N="$(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - <<'PY'
import os, psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent='cai' AND read_at IS NULL")
    print(cur.fetchone()[0])
PY
)"
fi
case "$N" in (*[!0-9]*|'') echo "nudge_cai: count must be numeric — free text is forbidden (R1)" >&2; exit 2;; esac

LINE="[cc-orchestrator relay] ${N} unread on the bus — drain agent_messages"
"$TMUX_BIN" has-session -t '=cai' 2>/dev/null || { echo "nudge_cai: no live cai session (log is durable; nothing lost)" >&2; exit 0; }
# '=cai:0.0': tmux 3.7a send-keys can't always resolve a bare '=cai' to a pane
"$TMUX_BIN" send-keys -t '=cai:0.0' -l "$LINE"
sleep 1
"$TMUX_BIN" send-keys -t '=cai:0.0' Enter
echo "nudged cai: $LINE"
