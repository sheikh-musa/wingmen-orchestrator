#!/usr/bin/env bash
# boot_quality.sh — ON-DEMAND launcher for cc-quality, the fleet's Head of Quality.
#
# Distinct from boot_fleet_health.sh (perpetual SRE): cc-quality is ON-DEMAND /
# scheduled per its charter (cond #1, CAI-RESP-776/777/778) — it is NOT a 24/7
# heartbeat lane and must not idle a Max seat (the fleet is shrinking consumer-token
# use, CAI-729->733). So this launcher has NO perpetual heartbeat loop and NO lease:
# it marks the agent working, launches an interactive claude that reads its charter
# + bus and does the assigned reviews/sweeps, and marks it offline on exit.
#
# Why a dedicated boot and NOT scripts/launch_dangerous_cc.sh (same reasoning as
# boot_cai.sh / boot_fleet_health.sh): the engineer launcher allocates a SUB-TAG
# (cc-quality-N) + engineer-lane machinery. cc-quality is a SINGLETON node —
# agent_id='cc-quality' EXACTLY, no sub-tag, no code-push machinery. Its home
# (~/wingmen/quality) carries a PINNED CLAUDE.md so an invocation via the
# orchestrator's absolute path can never resolve identity to the orchestrator's
# CLAUDE.md (the boot_cai identity-bug failure mode).
#
# Usage:  ./boot_quality.sh          # interactive, opus-4-8, on-demand (exits when done)
# Boot it under tmux on the Mac Mini:
#   tmux new-session -d -s quality -c ~/wingmen/quality ~/wingmen/orchestrator/scripts/boot_quality.sh
set -uo pipefail

# PINNED to cc-quality's own home (not derived from ${BASH_SOURCE[0]} dirname) so an
# invocation via the orchestrator's absolute path can't resolve the home to
# orchestrator/scripts and boot it with the ORCHESTRATOR's CLAUDE.md = a 2nd
# cc-orchestrator (the boot_cai identity outage). Pin to the home so path can't matter.
Q_DIR="$HOME/wingmen/quality"
ORCH_DIR="$HOME/wingmen/orchestrator"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
# MODEL resolution — precedence: MODEL env > durable pointer (.quality_model) > opus-4-8.
# The durable pointer mirrors .quality_default_token (and boot_nazim's .nazim_default_token):
# a per-body choice that STICKS across reboots, revert = `rm .quality_model`.
# WHY THIS EXISTS (op#13633, 2026-08-17): the operator pinned cc-quality to Opus 5 by writing
# .quality_model, which is the ENGINEER-LANE launcher's mechanism (launch_dangerous_cc.sh).
# cc-quality is a SINGLETON and boots here, which read MODEL env only — so the pin was INERT and
# would have silently done nothing. A pointer that looks set but is not read is worse than no
# pointer: it reports a choice that was never made. Same class as everything else found this
# session. Fail-safe: an absent/unreadable pointer falls through to the default.
_MODEL_FROM_ENV="${MODEL:-}"          # capture BEFORE defaulting, or env can never win
MODEL="claude-opus-4-8"
if [ -r "$HOME/wingmen/orchestrator/.quality_model" ]; then
    _QM="$(tr -d '[:space:]' < "$HOME/wingmen/orchestrator/.quality_model" 2>/dev/null || true)"
    if [ -n "${_QM:-}" ]; then
        MODEL="$_QM"
        echo "[boot_quality] durable model pointer applied (.quality_model -> $_QM)" >&2
    fi
fi
[ -n "$_MODEL_FROM_ENV" ] && MODEL="$_MODEL_FROM_ENV"
AGENT_ID="cc-quality"   # exact — singleton node, never a sub-tag

if [ ! -f "$Q_DIR/CLAUDE.md" ]; then
    echo "ERROR: $Q_DIR/CLAUDE.md missing — cc-quality identity home not set up." >&2
    exit 1
fi

# .env (DSN, OAuth token) lives in the orchestrator; cc-quality shares the substrate.
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
# OAuth account resolution — precedence: explicit OVERRIDE (live re-token) > durable
# pointer (.quality_default_token, reversible per-body default; op#11326 fleet flip,
# revert = `rm .quality_default_token`) > .env. FAIL-SAFE: absent pointer -> .env.
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE:-}" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"
elif [ -r "$ORCH_DIR/.quality_default_token" ]; then
    _QTOKF="$(tr -d '[:space:]' < "$ORCH_DIR/.quality_default_token" 2>/dev/null || true)"
    if [ -n "${_QTOKF:-}" ] && [ -r "$_QTOKF" ]; then
        export CLAUDE_CODE_OAUTH_TOKEN="$(cat "$_QTOKF")"
        echo "[boot_quality] durable token override applied (.quality_default_token -> $_QTOKF)" >&2
    fi
fi
# Max-subscription billing (verified pattern, see boot_fleet_health.sh):
#  1. Scrub ANTHROPIC_API_KEY so `claude` does not fall to metered API billing.
#  2. Keep CLAUDE_CODE_OAUTH_TOKEN (from .env) — tmux runs outside the GUI login,
#     so it cannot read the interactive /login OAuth from the Keychain.
unset ANTHROPIC_API_KEY
# auth_fp = sha256(effective launch token)[:12] — stable account fingerprint stamped
# into agent_status below so cc-quality is post-flip verifiable (op#11326).
AUTH_FP="$(printf '%s' "${CLAUDE_CODE_OAUTH_TOKEN:-}" | shasum -a 256 2>/dev/null | cut -c1-12)"
DSN="${DATABASE_URL:-${SUPABASE_DB_URL:-}}"
if [ -z "$DSN" ]; then
    echo "ERROR: DATABASE_URL not set in $ORCH_DIR/.env — cannot bring cc-quality online" >&2
    exit 1
fi

_sql() { "$VENV_PY" - "$@" <<'PY'
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
sql = sys.argv[1]
# agent_status identity trigger: app.current_agent_id GUC must == row's agent_id.
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-quality', true)")
    cur.execute(sql)
    conn.commit()
PY
}

# ── Pre-flight: the agents row (FK target for agent_messages) must exist ──────
REGISTERED="$("$VENV_PY" - <<'PY'
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT 1 FROM agents WHERE id='cc-quality'")
    print("yes" if cur.fetchone() else "no")
PY
)"
if [ "$REGISTERED" != "yes" ]; then
    echo "ERROR: agents.id='cc-quality' missing — run: (cd $ORCH_DIR && .venv/bin/python3 -m scripts.register_quality)" >&2
    exit 1
fi

# ── Bring cc-quality online (agent_status + agents). Exact agent_id. ──────────
# Self-register the tmux session for launchd-safe wake delivery (read from this pane).
Q_TMUX_SESSION="$(tmux display-message -p '#S' 2>/dev/null || true)"
_sql "
INSERT INTO agent_status (agent_id, base_agent_id, status, current_task, scope_repos, tmux_session, auth_fp, last_heartbeat, updated_at)
VALUES ('cc-quality','cc-quality','working','cc-quality on-demand review/sweep session', ARRAY['*']::text[], NULLIF('$Q_TMUX_SESSION',''), NULLIF('$AUTH_FP',''), now(), now())
ON CONFLICT (agent_id) DO UPDATE
  SET status='working', current_task='cc-quality on-demand review/sweep session',
      tmux_session=NULLIF('$Q_TMUX_SESSION',''),
      auth_fp=NULLIF('$AUTH_FP',''),
      last_heartbeat=now(), updated_at=now();
UPDATE agents SET status='active', last_heartbeat=now() WHERE id='cc-quality';
"
echo "▶ cc-quality online (on-demand): agent_status + agents set (model=$MODEL, dir=$Q_DIR)"

# ── Clean exit: mark offline (on-demand — no heartbeat loop to kill) ──────────
_handle_exit() {
    "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-quality', true)")
    cur.execute("UPDATE agent_status SET status='offline', current_task=NULL, last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-quality'")
    cur.execute("UPDATE agents SET status='idle' WHERE id='cc-quality'")
    conn.commit()
PY
    echo "▶ cc-quality offline (clean exit)."
}
trap '_handle_exit' EXIT

# ── Launch. CLAUDE.md in $Q_DIR auto-loads as project instructions; cc-quality
#    runs its own boot (confirm identity, read charter, reconcile bus, do work). ─
echo "▶ Launching claude --dangerously-skip-permissions --model $MODEL in $Q_DIR"
cd "$Q_DIR"
claude --dangerously-skip-permissions --model "$MODEL"
