#!/usr/bin/env bash
# lane_heartbeat.sh — HONEST heartbeat (CAI-RESP-381). Written by the AGENT on
# real turn completion (wired as a Claude Code Stop hook), NOT a blind 5-min
# timer. Pulses agents.last_heartbeat AND snapshots the lane's actual current
# activity into current_task — so the fleet console shows a TRUTHFUL "what each
# lane is doing now" instead of empty task + a meaningless timer pulse.
#
# A stale heartbeat now means "this agent has not completed a turn recently" =
# genuinely idle/stuck, which is the honest signal (vs the timer that pulsed
# 'active' forever). Never crashes a turn: every failure path is a silent no-op.
set -uo pipefail
ORCH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

AID="${CC_BASE_AGENT_ID:-${CC_AGENT_ID:-}}"
[ -n "$AID" ] || exit 0   # no resolved identity → no-op

# Best-effort: this lane's own pane, last meaningful activity line → current_task.
TASK=""
if [ -n "${TMUX:-}" ]; then
  TASK="$(tmux display-message -p -F '#{pane_id}' 2>/dev/null | xargs -I{} tmux capture-pane -t {} -p 2>/dev/null \
          | grep -aE '^[[:space:]]*[⏺✻✳·]|esc to interrupt' | tail -1 \
          | sed -E 's/^[^A-Za-z0-9]*//' | cut -c1-140)"
fi

set -a; source "$ORCH/.env" 2>/dev/null; set +a
AID="$AID" TASK="$TASK" "$ORCH/.venv/bin/python3" - <<'PY' 2>/dev/null || true
import os, psycopg
aid = os.environ["AID"]
task = (os.environ.get("TASK") or "").strip() or None
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if not dsn:
    raise SystemExit(0)
with psycopg.connect(dsn, connect_timeout=8) as c, c.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (aid,))
    # only overwrite current_task when we actually captured something
    if task:
        cur.execute("UPDATE agents SET last_heartbeat=now(), status='active', current_task=%s WHERE id=%s", (task, aid))
    else:
        cur.execute("UPDATE agents SET last_heartbeat=now(), status='active' WHERE id=%s", (aid,))
    c.commit()
PY
exit 0
