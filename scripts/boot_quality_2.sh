#!/usr/bin/env bash
# boot_quality_2.sh — ON-DEMAND launcher for cc-quality-2, the fleet's SECOND reviewer.
#
# WHY THIS EXISTS (orch-console #87390, 2026-10-31): cc-quality had ~12 PRs queued;
# the reviewer became the bottleneck of the build-review loop once the builders moved
# to DeepSeek. cc-quality-2 takes the cosem-adcda + cosem-platform PRs so the two
# reviewers run in parallel. Same charter, same standards, same five cai conditions.
#
# Deliberate differences from boot_quality.sh (each is load-bearing):
#   1. IDENTITY: agent_id='cc-quality-2' exactly (NOT a sub-tag). Its own home, own
#      tmux session, own agent_status row — never shares cc-quality's.
#   2. MODEL: read from .quality_2_model, NEVER .quality_model. Two reviewers on one
#      pointer would silently collapse the split. Pinned to an ANTHROPIC model so the
#      review stays independent of the DeepSeek builders it reviews.
#   3. TOKEN: read from .quality_2_default_token (Syed). NEVER Musa's — orch-console
#      #87390 is explicit. The name<->fp guard refuses a mislabelled key file rather
#      than silently running on the wrong account.
#
# Like cc-quality this lane is ON-DEMAND / scheduled (charter cond #1, CAI-RESP-776/
# 777/778): no perpetual lease, no 24/7 busywork. It marks itself working, does the
# assigned reviews, and marks offline on exit.
#
# Usage:  ./boot_quality_2.sh          # interactive, on-demand (exits when done)
#   tmux new-session -d -s quality-2 -c ~/wingmen/quality-2 ~/wingmen/quality-2/boot_quality_2.sh
set -uo pipefail

Q_DIR="$HOME/wingmen/quality-2"          # pinned to THIS body's home, never derived
ORCH_DIR="$HOME/wingmen/orchestrator"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
AGENT_ID="cc-quality-2"                  # exact — never a sub-tag

_MODEL_FROM_ENV="${MODEL:-}"
MODEL="claude-opus-4-8"
if [ -r "$ORCH_DIR/.quality_2_model" ]; then
    _QM="$(tr -d '[:space:]' < "$ORCH_DIR/.quality_2_model" 2>/dev/null || true)"
    if [ -n "${_QM:-}" ]; then
        MODEL="$_QM"
        echo "[boot_quality_2] durable model pointer applied (.quality_2_model -> $_QM)" >&2
    fi
fi
[ -n "$_MODEL_FROM_ENV" ] && MODEL="$_MODEL_FROM_ENV"

# Review independence is the whole reason this lane is on an Anthropic model. Refuse
# to boot onto a non-Anthropic one rather than review the DeepSeek builders' work with
# the same provider family that produced it.
case "$MODEL" in
    *deepseek*|*glm*|*qwen*)
        echo "[boot_quality_2] FATAL: model '$MODEL' is non-Anthropic — this lane exists to keep reviews INDEPENDENT of the deepseek builders (orch-console #87390). Refusing to boot." >&2
        exit 1 ;;
esac

if [ ! -f "$Q_DIR/CLAUDE.md" ]; then
    echo "ERROR: $Q_DIR/CLAUDE.md missing — cc-quality-2 identity home not set up." >&2
    exit 1
fi

set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
# Same prod-cred isolation as boot_quality.sh: this lane reviews code, it has no
# business holding a live GCP prod SA pointer. Left set, lane_prod_key_guard would
# (correctly) refuse every Bash call for this lane.
unset GOOGLE_APPLICATION_CREDENTIALS GOOGLE_CLOUD_PROJECT
export ZDOTDIR="$ORCH_DIR/deploy/agent-zdotdir"

# OAuth resolution — precedence: explicit override > durable pointer > .env.
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE:-}" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"
elif [ -r "$ORCH_DIR/.quality_2_default_token" ]; then
    _QTOKF="$(tr -d '[:space:]' < "$ORCH_DIR/.quality_2_default_token" 2>/dev/null || true)"
    if [ -n "${_QTOKF:-}" ] && [ -r "$_QTOKF" ]; then
        . "$ORCH_DIR/scripts/lib/token_file_guard.sh" || { echo "[boot_quality_2] FATAL: token_file_guard.sh missing" >&2; exit 1; }
        if ! _tfg_reason="$(token_file_guard "$_QTOKF" 2>&1)"; then
            token_guard_boot_refusal "$AGENT_ID" "$_QTOKF" "$_tfg_reason"; exit 1
        fi
        export CLAUDE_CODE_OAUTH_TOKEN="$(cat "$_QTOKF")"
        echo "[boot_quality_2] durable token override applied (.quality_2_default_token -> $_QTOKF)" >&2
    fi
fi
unset ANTHROPIC_API_KEY
export CC_AGENT_ID="$AGENT_ID"
export CC_BASE_AGENT_ID="$AGENT_ID"
unset ORCH_AGENT_ID
AUTH_FP="$(printf '%s' "${CLAUDE_CODE_OAUTH_TOKEN:-}" | shasum -a 256 2>/dev/null | cut -c1-12)"
DSN="${DATABASE_URL:-${SUPABASE_DB_URL:-}}"
if [ -z "$DSN" ]; then
    echo "ERROR: DATABASE_URL not set in $ORCH_DIR/.env — cannot bring cc-quality-2 online" >&2
    exit 1
fi

CLAUDE_BIN="$(command -v claude || true)"
if [ -z "$CLAUDE_BIN" ]; then
    for _c in "$HOME/.local/bin/claude" /usr/local/bin/claude /opt/homebrew/bin/claude; do
        [ -x "$_c" ] && CLAUDE_BIN="$_c" && break
    done
fi
if [ -z "$CLAUDE_BIN" ] || [ ! -x "$CLAUDE_BIN" ]; then
    echo "FATAL: claude binary not found on PATH ($PATH) or known fallback locations — refusing to boot" >&2
    exit 1
fi
echo "[boot_quality_2] claude resolved: $CLAUDE_BIN" >&2

_sql() { "$VENV_PY" - "$@" <<'PY'
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
sql = sys.argv[1]
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id', 'cc-quality-2', true)")
    cur.execute(sql)
    conn.commit()
PY
}

REGISTERED="$("$VENV_PY" - <<'PY'
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT 1 FROM agents WHERE id='cc-quality-2'")
    print("yes" if cur.fetchone() else "no")
PY
)"
if [ "$REGISTERED" != "yes" ]; then
    echo "ERROR: agents.id='cc-quality-2' missing — this lane needs its agents row before boot." >&2
    exit 1
fi

export Q2_TMUX_SESSION="${Q2_TMUX_SESSION:-quality-2}"
# auth_account is derived from the fp the token ACTUALLY hashed to, never a static
# label (orch-console #51865: a stale label mislabelled a re-tokened lane forever).
AUTH_ACCT="$(. "$ORCH_DIR/scripts/lib/token_file_guard.sh" 2>/dev/null; account_for_fp "$AUTH_FP" 2>/dev/null || true)"
_sql "
INSERT INTO agent_status (agent_id, base_agent_id, status, current_task, scope_repos, tmux_session, auth_account, auth_fp, host, last_heartbeat, updated_at)
VALUES ('cc-quality-2','cc-quality','working','cc-quality-2 on-demand review session (cosem-adcda + cosem-platform)', ARRAY['*']::text[], NULLIF('$Q2_TMUX_SESSION',''), NULLIF('$AUTH_ACCT',''), NULLIF('$AUTH_FP',''), 'Sheikhs-Mini', now(), now())
ON CONFLICT (agent_id) DO UPDATE
  SET base_agent_id='cc-quality',
      status='working', current_task='cc-quality-2 on-demand review session (cosem-adcda + cosem-platform)',
      tmux_session=NULLIF('$Q2_TMUX_SESSION',''),
      auth_account=NULLIF('$AUTH_ACCT',''), auth_fp=NULLIF('$AUTH_FP',''),
      host='Sheikhs-Mini', last_heartbeat=now(), updated_at=now();
UPDATE agents SET status='active', last_heartbeat=now(),
  display_name='cc-quality-2 — second reviewer (cosem-adcda + cosem-platform, Syed)'
WHERE id='cc-quality-2';
"
echo "▶ cc-quality-2 online (on-demand): agent_status + agents set (model=$MODEL, acct=${AUTH_ACCT:-unknown}, fp=$AUTH_FP, dir=$Q_DIR)"

_quality2_heartbeat_loop() {
    while true; do
        sleep 300
        "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if not dsn:
    raise SystemExit
try:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-quality-2',true)")
        cur.execute("UPDATE agent_status SET tmux_session=%s, last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-quality-2'",
                    (os.environ.get("Q2_TMUX_SESSION") or "quality-2",))
        cur.execute("UPDATE agents SET last_heartbeat=now() WHERE id='cc-quality-2'")
        conn.commit()
except Exception:
    pass
PY
    done
}
_quality2_heartbeat_loop &
HB_PID=$!
echo "▶ cc-quality-2 heartbeat loop started (pid=$HB_PID, 5-min cadence — auto-killed on exit)"

_handle_exit() {
    [ -n "${HB_PID:-}" ] && kill "$HB_PID" 2>/dev/null || true
    "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
try:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', 'cc-quality-2', true)")
        cur.execute("UPDATE agent_status SET status='offline', current_task=NULL, last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-quality-2'")
        cur.execute("UPDATE agents SET status='idle' WHERE id='cc-quality-2'")
        conn.commit()
except Exception:
    pass
PY
    echo "▶ cc-quality-2 offline (clean exit)."
}
trap '_handle_exit' EXIT

if [ ! -e "$Q_DIR/scripts/self_recycle.sh" ]; then
    echo "⚠ $Q_DIR/scripts has no self_recycle.sh — use an ABSOLUTE path" >&2
fi

echo "▶ Launching $CLAUDE_BIN --dangerously-skip-permissions --model $MODEL in $Q_DIR"
"$VENV_PY" "$ORCH_DIR/scripts/lib/ensure_lane_deny.py" --cwd "$Q_DIR" || true
cd "$Q_DIR"
source "$ORCH_DIR/scripts/lib/pin_singleton_window_size.sh"
"$CLAUDE_BIN" --dangerously-skip-permissions --model "$MODEL"
