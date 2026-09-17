#!/bin/bash
# irsyad_worker_supervisor.sh (gzb) — boot ONE elastic irsyad pool worker cc-irsyad-<N>.
#
# Unlike coord_supervisor.sh (an always-on standing lane under systemd Restart=always),
# this is BOOT-ON-DEMAND: the spin-actuator (scripts/irsyad_spin_worker.py) invokes it AFTER
# Nazim confirms a spin proposal. It boots the tmux session on the shared tmux server, submits
# the claim-build-loop prompt, and EXITS (no keepalive) — the pool is elastic and the worker
# winds itself down when the queue is empty (see prompts/irsyad_worker_prompt.md).
#
# Identity: CC_BASE_OVERRIDE=cc-irsyad (generic family) -> launch_dangerous_cc allocates the
# smallest-free sub-tag cc-irsyad-<N>. OAuth: the session name 'irsyad-worker-<N>' has family
# 'irsyad' (lane_token_resolver.family_of) -> .group_default_token.irsyad -> musa2 pool.
#
# Usage: irsyad_worker_supervisor.sh <N>        (N = pool slot, 1..MAX_LANES)
#   WT for slot N = $HOME/wingmen/projects/ihsanos-irsyad.wt-worker<N> (actuator creates it).
set -uo pipefail

N="${1:-}"
[ -n "$N" ] || { echo "[worker_supervisor] FATAL: usage: $0 <N>" >&2; exit 2; }
case "$N" in (*[!0-9]*|'') echo "[worker_supervisor] FATAL: N must be a positive integer" >&2; exit 2;; esac

SESSION="irsyad-worker-${N}"
WT="$HOME/wingmen/projects/ihsanos-irsyad.wt-worker${N}"
ORCH_DIR="$HOME/wingmen/orchestrator"
LAUNCHER="$ORCH_DIR/scripts/launch_dangerous_cc.sh"
PROMPT_FILE="$ORCH_DIR/prompts/irsyad_worker_prompt.md"

[ -d "$WT" ]        || { echo "[worker_supervisor] FATAL: worktree missing (actuator creates it): $WT" >&2; exit 1; }
[ -x "$LAUNCHER" ]  || { echo "[worker_supervisor] FATAL: launcher not executable: $LAUNCHER" >&2; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "[worker_supervisor] FATAL: worker prompt missing: $PROMPT_FILE" >&2; exit 1; }

# SHARED tmpfs secrets only (RO DSNs + substrate). NEVER private/write_dsn.env (trips the
# launcher's CAI-1225 L2 write-DSN-in-env guard). Same discipline as coord_supervisor.sh.
set -a; . /dev/shm/wingmen-secrets/.env 2>/dev/null; set +a
unset ANTHROPIC_API_KEY                 # Max OAuth only; never metered API.
export PATH="$HOME/.local/bin:$PATH"

TMUX_BIN="$(command -v tmux || true)"
if [ -z "$TMUX_BIN" ]; then
  for _t in /usr/bin/tmux /usr/local/bin/tmux "$HOME/.local/bin/tmux"; do
    [ -x "$_t" ] && TMUX_BIN="$_t" && break
  done
fi
[ -n "$TMUX_BIN" ] || { echo "[worker_supervisor] FATAL: tmux not found" >&2; exit 1; }

# --continue is CONDITIONAL: a fresh worktree has no prior conversation for this project dir,
# and --continue would make claude EXIT on boot. Claude munges the cwd -> project-dir name by
# replacing '/' and '.' with '-'.
CONT_ARGS=()
PROJ_KEY="$(printf '%s' "$WT" | sed 's/[/.]/-/g')"
PROJ_DIR="$HOME/.claude/projects/${PROJ_KEY}"
if ls "$PROJ_DIR/"*.jsonl >/dev/null 2>&1; then CONT_ARGS=(-- --continue); fi

# Boot the session if absent (idempotent). CC_BASE_OVERRIDE passed via `-e` (a pane inherits
# the tmux SERVER env, not this process's). Write-DSNs unset in the pane wrapper (CAI-1225
# defense-in-depth), exactly as coord_supervisor.sh.
if ! "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  echo "[worker_supervisor] booting $SESSION (cc-irsyad worker, slot $N) at $(date -u +%FT%TZ)"
  "$TMUX_BIN" new-session -d -s "$SESSION" -x 220 -y 50 -c "$WT" \
    -e "CC_BASE_OVERRIDE=cc-irsyad" \
    -- bash -lc 'unset GOUMLYNE_DATABASE_URL IHSANOS_PROD_DATABASE_URL IHSANOS_SUPABASE_SERVICE_KEY; exec "$@"' _ \
       "$LAUNCHER" "${CONT_ARGS[@]+"${CONT_ARGS[@]}"}"
else
  echo "[worker_supervisor] $SESSION already live — will (re)deliver the worker prompt"
fi

# IDENTITY READ-BACK + UNIQUENESS ASSERT (Nazim #40525): launch_dangerous_cc ALWAYS
# auto-allocates the sub-tag (smallest-free scan; ignores any pre-set CC_AGENT_ID), so the
# worker's real bus identity is NOT the pool-slot number. Read the allocated sub-tag back from
# agent_status for THIS session, assert exactly ONE live row carries it (no collision with the
# tabung cc-irsyad-1 or any other body), and inject THAT id into the prompt — so prompt identity
# == bus identity. Without a unique read-back we REFUSE to nudge (identity unverified).
VENV_PY="$ORCH_DIR/.venv/bin/python3"
AGENT_ID=""
for _i in 1 2 3 4 5 6 7 8 9 10; do
  if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
    AGENT_ID="$("$VENV_PY" - "$SESSION" <<'PY' 2>/dev/null
import os,sys,psycopg
sess=sys.argv[1]
dsn=os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
try:
    with psycopg.connect(dsn,connect_timeout=10) as c, c.cursor() as cur:
        cur.execute("SELECT agent_id FROM agent_status WHERE tmux_session=%s "
                    "AND last_heartbeat>now()-interval '10 minutes'", (sess,))
        rows=cur.fetchall()
        if len(rows)!=1:
            sys.exit(0)                       # 0 or >1 rows for this session -> not yet / ambiguous
        aid=rows[0][0]
        cur.execute("SELECT count(*) FROM agent_status WHERE agent_id=%s "
                    "AND last_heartbeat>now()-interval '10 minutes'", (aid,))
        if cur.fetchone()[0]==1:              # this identity is held by exactly ONE live body
            print(aid)
except Exception:
    pass
PY
)"
    [ -n "$AGENT_ID" ] && break
  fi
  sleep 8
done
if [ -z "$AGENT_ID" ]; then
  echo "[worker_supervisor] FATAL: no UNIQUE allocated sub-tag for $SESSION in agent_status — REFUSING to nudge (identity unverified / possible collision)" >&2
  exit 3
fi
echo "[worker_supervisor] $SESSION allocated bus identity: $AGENT_ID (unique — verified)"

# Deliver the claim-build-loop prompt via the VERIFIED-SUBMIT nudge, pinning the REAL identity.
NUDGE="You are elastic irsyad pool worker ${AGENT_ID}. That is your BUS IDENTITY — use EXACTLY '${AGENT_ID}' as claimed_by on the queue and as from_agent on every bus post; NEVER the session/slot name and NEVER a different sub-tag. Read ${PROMPT_FILE} and run that claim-build-loop NOW against public.coord_dispatch_queue: atomically claim one unclaimed row older than 120s, read its spec_ref bus msg, build + PR per irsyad lane discipline (console-gate, NEVER self-merge, NEVER push to main), mark done_at, re-poll; wind down after 3 empty polls. Report boot + each claim/done + wind-down to orch-console and cc-irsyad-coord AS ${AGENT_ID}. You are a POOL worker, not the coordinator."
for _i in 1 2 3; do
  if bash "$ORCH_DIR/scripts/lane_nudge.sh" "$SESSION" "$NUDGE"; then
    echo "[worker_supervisor] worker prompt delivered to $SESSION as $AGENT_ID"; exit 0
  fi
  sleep 8
done
echo "[worker_supervisor] WARN: could not verify prompt delivery to $SESSION after retries — actuator should escalate P1" >&2
exit 3
