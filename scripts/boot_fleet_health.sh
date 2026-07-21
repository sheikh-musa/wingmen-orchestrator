#!/usr/bin/env bash
# boot_fleet_health.sh — perpetual launcher for cc-fleet-health, the fleet's SRE.
#
# The first dedicated-PERSISTENT agent under the 2026-07-21 target topology
# (reports/fleet-target-topology-20260721.md): it OWNS the fleet's detect->act +
# self-healing loops so routine upkeep stops routing through Nazim (console) or
# the hub. Conservative first pass — monitoring + safe upkeep + escalation only;
# destructive self-healing (auto context-reset) stays gated/dry-run until armed.
#
# Why a dedicated boot and NOT scripts/launch_dangerous_cc.sh (same reasoning as
# boot_cai.sh): the engineer launcher resolves identity via auto_agent_id which
# allocates a SUB-TAG (cc-fleet-health-N) and wires engineer-lane machinery
# (code-push/Vercel/cc_work_sessions). cc-fleet-health is a SINGLETON SRE node —
# agent_id='cc-fleet-health' EXACTLY, no sub-tag, no code-push. This reuses the
# launcher's heartbeat + clean-exit PATTERN but pins identity and drops the lane
# machinery. ZERO schema changes (uses existing agents / agent_status rows;
# registration migration scripts/register_fleet_health.py seeds them once).
#
# Usage:  ./boot_fleet_health.sh        # interactive, opus-4-8, perpetual
#
# Boot it under tmux on the Mac Mini (host = Sheikhs-Mini) on the Max sub:
#   tmux new-session -d -s fleet-health -c ~/wingmen/fleet-health \
#       ~/wingmen/fleet-health/boot_fleet_health.sh
set -uo pipefail

# PINNED to the SRE's own home (not derived from ${BASH_SOURCE[0]} dirname) so an
# invocation via the orchestrator's absolute path can't resolve the home to
# orchestrator/scripts and boot it with the ORCHESTRATOR's CLAUDE.md = a 2nd
# cc-orchestrator (the silent-governance-outage failure mode boot_cai.sh hit on
# 2026-06-20). Pin to the home so invocation path can't matter.
FH_DIR="$HOME/wingmen/fleet-health"
ORCH_DIR="$HOME/wingmen/orchestrator"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
MODEL="${MODEL:-claude-opus-4-8}"
AGENT_ID="cc-fleet-health"   # exact — singleton SRE node, never a sub-tag

# .env (DSN, OAuth token) lives in the orchestrator; the SRE shares the substrate.
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
# Per-lane OAuth token override (e.g. a donor/loaner account during a cap crunch).
# Applied AFTER the .env source so it wins; distinct var name so `set -a; . .env`
# cannot clobber it. Unset for normal boots -> no effect on billing.
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE:-}" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"
fi
# Max-subscription billing — two parts, both load-bearing (verified 2026-06-17):
#  1. Scrub ANTHROPIC_API_KEY: .env carries it for the orch's own API calls, but
#     a present ANTHROPIC_API_KEY makes `claude` use metered API-usage billing.
#  2. Keep CLAUDE_CODE_OAUTH_TOKEN (also from .env): this runs under tmux OUTSIDE
#     the GUI login session, so it can't read the interactive `/login` OAuth from
#     the Keychain — without this sk-ant-oat token it falls back to API billing.
#     The token bills to the Max subscription.
unset ANTHROPIC_API_KEY
DSN="${DATABASE_URL:-${SUPABASE_DB_URL:-}}"
if [ -z "$DSN" ]; then
    echo "ERROR: DATABASE_URL not set in $ORCH_DIR/.env — cannot bring cc-fleet-health online" >&2
    exit 1
fi

_sql() { "$VENV_PY" - "$@" <<'PY'
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
sql = sys.argv[1]
# Identity trigger (trg_agent_status_identity): every agent_status write requires
# the app.current_agent_id GUC == row's agent_id, SET LOCAL within the same TX.
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-fleet-health', true)")
    cur.execute(sql)
    conn.commit()
PY
}

# ── Pre-flight: the agents row (FK target for agent_messages) must exist ──────
# scripts/register_fleet_health.py seeds agents + fleet_lanes once. Fail LOUD if
# it was never run — an unregistered SRE can't post bus rows (FK violation).
REGISTERED="$("$VENV_PY" - <<'PY'
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT 1 FROM agents WHERE id='cc-fleet-health'")
    print("yes" if cur.fetchone() else "no")
PY
)"
if [ "$REGISTERED" != "yes" ]; then
    echo "ERROR: agents.id='cc-fleet-health' missing — run: (cd $ORCH_DIR && .venv/bin/python3 -m scripts.register_fleet_health)" >&2
    exit 1
fi

# ── Bring cc-fleet-health online (agent_status + agents). Exact agent_id. ─────
# base_agent_id='cc-fleet-health' satisfies the prefix CHECK (base == id with any
# trailing -N stripped; there is none). Self-register the tmux session for
# launchd-safe wake delivery — read from inside this pane.
FH_TMUX_SESSION="$(tmux display-message -p '#S' 2>/dev/null || true)"
_sql "
INSERT INTO agent_status (agent_id, base_agent_id, status, current_task, scope_repos, tmux_session, last_heartbeat, updated_at)
VALUES ('cc-fleet-health','cc-fleet-health','working','cc-fleet-health SRE perpetual health lane', ARRAY['*']::text[], NULLIF('$FH_TMUX_SESSION',''), now(), now())
ON CONFLICT (agent_id) DO UPDATE
  SET status='working', current_task='cc-fleet-health SRE perpetual health lane',
      tmux_session=NULLIF('$FH_TMUX_SESSION',''),
      last_heartbeat=now(), updated_at=now();
UPDATE agents SET status='active', last_heartbeat=now() WHERE id='cc-fleet-health';
"
echo "▶ cc-fleet-health online: agent_status + agents heartbeat set (model=$MODEL, dir=$FH_DIR)"

# ── Background heartbeat (5-min), auto-killed on exit ────────────────────────
_heartbeat_loop() {
    while true; do
        sleep 300
        "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-fleet-health', true)")
    cur.execute("UPDATE agent_status SET last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-fleet-health'")
    cur.execute("UPDATE agents SET last_heartbeat=now() WHERE id='cc-fleet-health'")
    conn.commit()
PY
    done
}
_heartbeat_loop &
HB_PID=$!

# ── Clean exit: kill heartbeat, mark offline ─────────────────────────────────
_handle_exit() {
    [ -n "${HB_PID:-}" ] && kill "$HB_PID" 2>/dev/null || true
    "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-fleet-health', true)")
    cur.execute("UPDATE agent_status SET status='offline', current_task=NULL, last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-fleet-health'")
    cur.execute("UPDATE agents SET status='idle' WHERE id='cc-fleet-health'")
    conn.commit()
PY
    echo "▶ cc-fleet-health offline (clean exit)."
}
trap '_handle_exit' EXIT

# ── Launch. CLAUDE.md in $FH_DIR auto-loads as project instructions; the SRE
#    runs its own boot sequence (charter §Boot) — inbox, charter, health pass. ─
echo "▶ Launching claude --dangerously-skip-permissions --model $MODEL in $FH_DIR"
cd "$FH_DIR"
claude --dangerously-skip-permissions --model "$MODEL"
