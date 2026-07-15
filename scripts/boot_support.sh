#!/usr/bin/env bash
# boot_support.sh — perpetual launcher for cc-support, the always-on irsyad/Gazzabyte
# client-support agent (op#4443/4493/4537, spec reports/cc-support-v1-spec-20260715.md).
#
# Mirrors boot_cai.sh's heartbeat + clean-exit pattern, pinned to agent_id='cc-support'.
# cc-support is NOT an orch body (holds no pens) and does NOT ship code — it answers the
# irsyad support group, gated. Boots in PHASE A (supervised: drafts-only, hub approves).
#
# Usage:  ./boot_support.sh        # interactive, opus-4-8, perpetual
set -uo pipefail

# PINNED to cc-support's home (same lesson as boot_cai.sh: a dirname-derived dir booted
# the wrong CLAUDE.md once). Its own CLAUDE.md = the support identity/prompt.
SUPPORT_DIR="$HOME/wingmen/wingmen-support"
ORCH_DIR="$HOME/wingmen/orchestrator"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
MODEL="${MODEL:-claude-opus-4-8}"
AGENT_ID="cc-support"

# .env (DSNs etc.) lives in the orchestrator; cc-support shares the substrate + reads the
# read-only goumlyne DSN (SUPPORT_READONLY_GOUMLYNE_DSN) when provisioned.
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE:-}" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"
fi
# Max billing (op#4449): scrub ANTHROPIC_API_KEY off the shell AND the tmux server-global
# (tmux copies the server-global into every pane, overriding a shell unset). Keep
# CLAUDE_CODE_OAUTH_TOKEN (Max). Verify post-boot via `ps eww <pane_pid>` (key absent=Max).
unset ANTHROPIC_API_KEY
tmux setenv -gu ANTHROPIC_API_KEY 2>/dev/null || true
DSN="${DATABASE_URL:-${SUPABASE_DB_URL:-}}"
if [ -z "$DSN" ]; then
    echo "ERROR: DATABASE_URL not set in $ORCH_DIR/.env — cannot bring cc-support online" >&2
    exit 1
fi

_sql() { "$VENV_PY" - "$@" <<'PY'
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
sql = sys.argv[1]
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-support', true)")
    cur.execute(sql)
    conn.commit()
PY
}

# ── Bring cc-support online (agent_status + agents), exact agent_id='cc-support'. ──
SUPPORT_TMUX_SESSION="$(tmux display-message -p '#S' 2>/dev/null || true)"
_sql "
INSERT INTO agent_status (agent_id, base_agent_id, status, current_task, scope_repos, tmux_session, last_heartbeat, updated_at)
VALUES ('cc-support','cc-support','working','cc-support Phase A (supervised drafts) — irsyad/Gazzabyte group', ARRAY['irsyad']::text[], NULLIF('$SUPPORT_TMUX_SESSION',''), now(), now())
ON CONFLICT (agent_id) DO UPDATE
  SET status='working', current_task='cc-support Phase A (supervised drafts) — irsyad/Gazzabyte group',
      tmux_session=NULLIF('$SUPPORT_TMUX_SESSION',''),
      last_heartbeat=now(), updated_at=now();
UPDATE agents SET status='active', last_heartbeat=now() WHERE id='cc-support';
"
echo "▶ cc-support online: agent_status + agents heartbeat set (model=$MODEL, dir=$SUPPORT_DIR)"

# ── Background heartbeat (5-min), auto-killed on exit ────────────────────────
_heartbeat_loop() {
    while true; do
        sleep 300
        "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-support', true)")
    cur.execute("UPDATE agent_status SET last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-support'")
    cur.execute("UPDATE agents SET last_heartbeat=now() WHERE id='cc-support'")
    conn.commit()
PY
    done
}
_heartbeat_loop &
HB_PID=$!

_handle_exit() {
    [ -n "${HB_PID:-}" ] && kill "$HB_PID" 2>/dev/null || true
    "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-support', true)")
    cur.execute("UPDATE agent_status SET status='offline', current_task=NULL, last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-support'")
    cur.execute("UPDATE agents SET status='idle' WHERE id='cc-support'")
    conn.commit()
PY
    echo "▶ cc-support offline (clean exit)."
}
trap '_handle_exit' EXIT

echo "▶ Launching claude --dangerously-skip-permissions --model $MODEL in $SUPPORT_DIR (Phase A)"
cd "$SUPPORT_DIR"
claude --dangerously-skip-permissions --model "$MODEL"
