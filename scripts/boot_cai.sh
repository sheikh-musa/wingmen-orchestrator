#!/usr/bin/env bash
# boot_cai.sh — perpetual launcher for cc-cai, the always-on strategic body of `cai`.
#
# PROPOSED (pending cai's ratification of the launch path — see thread 8d400ea9).
# Why a dedicated boot and NOT scripts/launch_dangerous_cc.sh:
#   The engineer launcher resolves identity via scripts.lib.auto_agent_id, which
#   (a) only maps `cc-%` families (cai is not one) and (b) allocates a SUB-TAG
#   `cai-N`. CAI-RESP-254 + CLAUDE.md §6.7 require agent_id='cai' EXACTLY, as a
#   singleton — no sub-tags, no family FK, no code-push/Vercel/cc_work_sessions
#   (cai adjudicates; it does not ship code). So this reuses the launcher's
#   heartbeat + clean-exit PATTERN but pins identity to cai/cai and drops the
#   engineer-lane machinery. ZERO schema changes (uses existing agents /
#   agent_status rows, per CAI-RESP-254).
#
# Usage:  ./boot_cai.sh        # interactive, opus-4-8, perpetual
set -uo pipefail

# PINNED (not derived from ${BASH_SOURCE[0]} dirname): running this via the
# orchestrator's absolute path resolved CAI_DIR to orchestrator/scripts and
# booted cai with the ORCHESTRATOR's CLAUDE.md = a 2nd cc-orchestrator (silent
# governance outage 2026-06-20). Pin to cai's home so invocation path can't matter.
CAI_DIR="$HOME/wingmen/wingmen-cai"
ORCH_DIR="$HOME/wingmen/orchestrator"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
MODEL="${MODEL:-${CAI_MODEL:-claude-opus-4-8}}"
AGENT_ID="cai"   # exact — singleton strategic node, never a sub-tag

# .env (DSN etc.) lives in the orchestrator; cai shares the substrate.
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
# Per-lane OAuth token override (e.g. a donor/loaner account during a cap crunch).
# Applied AFTER the .env source so it wins; distinct var name so `set -a; . .env`
# cannot clobber it. Unset for normal boots → no effect on billing.
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE:-}" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"
fi
# Max-subscription billing for cai — two parts, both load-bearing:
#  1. Scrub ANTHROPIC_API_KEY: .env carries it for the orch's own API calls, but
#     a present ANTHROPIC_API_KEY makes `claude` use metered API-usage billing.
#  2. Keep CLAUDE_CODE_OAUTH_TOKEN (also from .env): cai runs under tmux OUTSIDE
#     the GUI login session, so it can't read the interactive `/login` OAuth from
#     the Keychain — without this sk-ant-oat token it falls back to API billing.
#     The token bills to the Max subscription. (Verified 2026-06-17.)
unset ANTHROPIC_API_KEY
DSN="${DATABASE_URL:-${SUPABASE_DB_URL:-}}"
if [ -z "$DSN" ]; then
    echo "ERROR: DATABASE_URL not set in $ORCH_DIR/.env — cannot bring cai online" >&2
    exit 1
fi

_sql() { "$VENV_PY" - "$@" <<'PY'
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
sql = sys.argv[1]
# BUG-024/ARCH-035 identity trigger: every agent_status/agents write requires the
# app.current_agent_id GUC == row's agent_id, SET LOCAL within the same transaction.
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
    cur.execute(sql)
    conn.commit()
PY
}

# ── Bring cai online (agent_status + agents). Exact agent_id='cai'. ──────────
# base_agent_id='cai' satisfies the prefix CHECK (base == id with -N stripped).
# Self-register the tmux session for #111 launchd-safe wake delivery (cai is in
# the wake set, CAI-RESP-255 #3) — read from inside cai's own pane.
CAI_TMUX_SESSION="$(tmux display-message -p '#S' 2>/dev/null || true)"
_sql "
INSERT INTO agent_status (agent_id, base_agent_id, status, current_task, scope_repos, tmux_session, last_heartbeat, updated_at)
VALUES ('cai','cai','working','cc-cai perpetual strategic lane', ARRAY['*']::text[], NULLIF('$CAI_TMUX_SESSION',''), now(), now())
ON CONFLICT (agent_id) DO UPDATE
  SET status='working', current_task='cc-cai perpetual strategic lane',
      tmux_session=NULLIF('$CAI_TMUX_SESSION',''),
      last_heartbeat=now(), updated_at=now();
UPDATE agents SET status='active', last_heartbeat=now() WHERE id='cai';
"
echo "▶ cai online: agent_status + agents heartbeat set (model=$MODEL, dir=$CAI_DIR)"

# ── Background heartbeat (5-min), auto-killed on exit ────────────────────────
_heartbeat_loop() {
    while true; do
        sleep 300
        "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
    cur.execute("UPDATE agent_status SET last_heartbeat=now(), updated_at=now() WHERE agent_id='cai'")
    cur.execute("UPDATE agents SET last_heartbeat=now() WHERE id='cai'")
    conn.commit()
PY
    done
}
_heartbeat_loop &
HB_PID=$!

# ── Clean exit: kill heartbeat, mark cai offline ────────────────────────────
_handle_exit() {
    [ -n "${HB_PID:-}" ] && kill "$HB_PID" 2>/dev/null || true
    "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
    cur.execute("UPDATE agent_status SET status='offline', current_task=NULL, last_heartbeat=now(), updated_at=now() WHERE agent_id='cai'")
    cur.execute("UPDATE agents SET status='idle' WHERE id='cai'")
    conn.commit()
PY
    echo "▶ cai offline (clean exit)."
}
trap '_handle_exit' EXIT

# ── Launch. CLAUDE.md in $CAI_DIR auto-loads as project instructions; cai runs
#    its own boot SQL (CLAUDE.md §6.1) — boot_briefing, open windows, inbox. ──
echo "▶ Launching claude --dangerously-skip-permissions --model $MODEL in $CAI_DIR"
cd "$CAI_DIR"
claude --dangerously-skip-permissions --model "$MODEL"
