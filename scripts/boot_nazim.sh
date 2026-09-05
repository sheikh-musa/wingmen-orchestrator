#!/usr/bin/env bash
# boot_nazim.sh — launch the Nazim console session (ORCH-TOPOLOGY-001 console
# body, agent_id orch-console) on this host. Sources .env for the body-scoping
# vars (ORCH_BODY_ROLE=console, ORCH_AGENT_ID=orch-console, ORCH_TMUX_SESSION=nazim)
# and the Max OAuth token, then runs claude. This is NOT a lane — no cc-* family
# identity allocation (that's launch_dangerous_cc.sh, which fail-aborts on the
# orchestrator dir). Intended as the command a detached `nazim` tmux session runs:
#   tmux new-session -d -s nazim -c ~/wingmen/orchestrator scripts/boot_nazim.sh
# Any args are passed through to claude (e.g. --resume on a restart).
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"

# Body-scoping + OAuth token come from .env. `set -a` exports every assignment so
# the console identity is visible to operator_log / orch_lease inside the session.
set -a
# shellcheck disable=SC1091
source "$ORCH_DIR/.env"
set +a

# CAI-1225: source the RESTRICTED write-DSN store (the live-write GOUMLYNE/ceayj DSNs are
# NOT in the shared .env — lanes/auditors never see them). Only console/cai boots + the 2
# named writer tools may read it; the store's L3 tripwire refuses (return-based, non-fatal to
# this sourced boot) unless WRITE_DSN_ALLOWED=1 is set here first. Loud-but-non-fatal: no set -e.
export WRITE_DSN_ALLOWED=1
set -a; . "$HOME/.wingmen/private/write_dsn.env"; set +a
unset WRITE_DSN_ALLOWED

# OAuth account resolution — precedence: explicit OVERRIDE (a live re-token) >
# durable pointer (.nazim_default_token, reversible per-body default; op#9920/#11326,
# revert = `rm .nazim_default_token`, mirrors the hub's .orch_default_token) > .env.
# FAIL-SAFE: an absent/unreadable pointer or token falls through to .env — never
# offline. (OVERRIDE now takes precedence so a switch_singleton-style live re-token
# is not clobbered by a stale pointer.)
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE:-}" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE"
elif [ -r "$ORCH_DIR/.nazim_default_token" ]; then
    _NZTOKF="$(tr -d '[:space:]' < "$ORCH_DIR/.nazim_default_token" 2>/dev/null || true)"
    if [ -n "${_NZTOKF:-}" ] && [ -r "$_NZTOKF" ]; then
        export CLAUDE_CODE_OAUTH_TOKEN="$(cat "$_NZTOKF")"
        echo "[boot_nazim] token override applied (.nazim_default_token -> $_NZTOKF)" >&2
    fi
fi

# Max-subscription billing: scrub the metered API key (its mere presence flips
# `claude` to API billing); keep CLAUDE_CODE_OAUTH_TOKEN (tmux/headless can't read
# the GUI-login OAuth from the Keychain).
unset ANTHROPIC_API_KEY

# auth_fp = sha256(effective launch token)[:12] — stamp orch-console's account onto
# its agent_status row so the console is post-flip verifiable (op#11326). UPDATE-only
# + best-effort: it stamps the EXISTING self-registered row (never creates a partial
# row, never fails the boot). NOTE: orch-console's own self-registration must PRESERVE
# auth_fp (not clobber it to NULL) for this to persist across the body's re-register.
AUTH_FP="$(printf '%s' "${CLAUDE_CODE_OAUTH_TOKEN:-}" | shasum -a 256 2>/dev/null | cut -c1-12)"
if [ -n "${AUTH_FP:-}" ] && [ -x "$ORCH_DIR/.venv/bin/python3" ]; then
    "$ORCH_DIR/.venv/bin/python3" - "$AUTH_FP" <<'PY' 2>/dev/null || true
import os, sys
try:
    import psycopg
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    fp = sys.argv[1]
    if dsn and fp:
        with psycopg.connect(dsn, connect_timeout=10) as c, c.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
            cur.execute("UPDATE agent_status SET auth_fp=%s, updated_at=now() WHERE agent_id='orch-console'", (fp,))
            c.commit()
except Exception:
    pass
PY
fi

# Resolve claude robustly — the Mini keeps it at ~/.local/bin, off the non-login PATH.
CLAUDE_BIN="$(command -v claude || true)"
if [[ -z "$CLAUDE_BIN" ]]; then
    for _c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
        [[ -x "$_c" ]] && CLAUDE_BIN="$_c" && break
    done
fi
[[ -n "$CLAUDE_BIN" ]] || { echo "[boot_nazim] claude binary not found" >&2; exit 1; }

# ── Background heartbeat (5-min) + dead-man's-switch (op#11427 f/u, CAI-RESP-791) ─
# orch-console had NO periodic heartbeat writer (this boot exec'd claude directly),
# so agent_status.last_heartbeat froze between reboots — a DEAD liveness signal on the
# operator's lifeline. Mirror boot_cai.sh: a 5-min loop refreshes last_heartbeat as a
# CHILD of this script, so it dies when the body dies (dead-man's-switch).
# CRITICAL: the write MUST set the identity GUC in the SAME txn as the UPDATE, or the
# agent_status identity trigger (BUG-024/ARCH-035) REJECTS it and the heartbeat never
# lands (a fix that doesn't fix). This REQUIRES foreground claude (NOT exec) below, so
# this shell survives to own the loop + fire the EXIT trap; an exec'd claude would
# orphan the loop into a liveness-LIE (heartbeat outliving a dead body).
VENV_PY="$ORCH_DIR/.venv/bin/python3"
_console_heartbeat_loop() {
    while true; do
        sleep 300
        "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
    # Re-assert status='working' every beat (self-healing): the exit trap stamps
    # 'offline' on recycle, and nothing else clears it — without this the fresh
    # body's lifeline row reads 'offline' for its whole life (SRE #30204).
    cur.execute("UPDATE agent_status SET status='working', last_heartbeat=now(), updated_at=now() WHERE agent_id='orch-console'")
    cur.execute("UPDATE agents SET status='active', last_heartbeat=now() WHERE id='orch-console'")
    conn.commit()
PY
    done
}

# Immediate boot re-assert to 'working' — clears the exit-trap 'offline' NOW rather
# than waiting up to one heartbeat interval (~300s) with the lifeline reading dead.
_console_assert_alive() {
    "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
    cur.execute("UPDATE agent_status SET status='working', current_task='orch-console CTO console (Mac Mini, tmux nazim)', last_heartbeat=now(), updated_at=now() WHERE agent_id='orch-console'")
    cur.execute("UPDATE agents SET status='active', last_heartbeat=now() WHERE id='orch-console'")
    conn.commit()
PY
}
HB_PID=""
if [ -x "$VENV_PY" ]; then
    _console_assert_alive
    _console_heartbeat_loop &
    HB_PID=$!
else
    echo "[boot_nazim] WARN: $VENV_PY missing — heartbeat loop NOT started (console hb stays stale)" >&2
fi

# Clean exit: stop the heartbeat, then mark console offline. NOTE (#17025 interaction):
# this writes a transient status='offline' during the seconds between this body exiting
# and the launchd waiter (boot_nazim_session.sh) relaunching it. The watchdog's #17025
# live-session carve-out still governs PAGING (it gates on a live tmux session, not this
# row alone), so the blip does not itself page. The fresh boot NOW genuinely re-asserts
# status='working' — immediately via _console_assert_alive and every beat in the hb loop
# (SRE #30204: the prior claim of a re-assert was false, so the row stayed 'offline' for
# the whole life of the fresh body). The offline write is kept so a clean recycle reads
# honestly-offline in the relaunch gap; real death is caught by heartbeat staleness.
_console_boot_exit() {
    [ -n "${HB_PID:-}" ] && kill "$HB_PID" 2>/dev/null || true
    "$VENV_PY" - <<'PY' 2>/dev/null || true
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
    cur.execute("UPDATE agent_status SET status='offline', current_task=NULL, last_heartbeat=now(), updated_at=now() WHERE agent_id='orch-console'")
    cur.execute("UPDATE agents SET status='idle' WHERE id='orch-console'")
    conn.commit()
PY
}
trap '_console_boot_exit' EXIT

# Model is .env-driven (NAZIM_MODEL) so the launchd KeepAlive waiter's no-arg
# auto-restart honors the operator's choice too — the old hardcoded flag pinned
# every auto-restart to 4.8 and left the operator no way in without racing the
# waiter (op#7004). Default = Opus 4.8 (parity with the hub orch + cai). A caller
# --model in "$@" still comes later on argv and wins (claude last-wins parsing).
NAZIM_MODEL="${NAZIM_MODEL:-claude-opus-4-8}"

# ── SELF-FIRING RECONSTITUTION KICK (operator-caught 2026-09-05, op_msg 19154:
# "again you are recycling without reconstituting") ──────────────────────────────
# A fresh KILL+RELAUNCH (this path) lands claude at the welcome banner with the
# SessionStart hook's reconstitution CONTEXT loaded but NO turn started — the body
# then sits IDLE at the welcome screen until an EXTERNAL nudge (a bus item or an
# operator DM) happens to arrive and kick a turn. That external-nudge dependency IS
# the "recycling without reconstituting" the operator kept catching: a quiet inbox
# leaves a fresh console idle indefinitely (and the operator had to poke it himself).
# The in-place reset_nazim.sh path already send-keys an explicit boot kick after its
# /clear; this relaunch path never had one. So background a one-shot that waits for
# the banner to paint, confirms no turn already started (reusing the fleet's ONE busy
# definition so we never send-keys into a live turn — the jam hazard reset_nazim
# guards against), takes the same fire-window lock the nudgers consult (so a nudge
# can't interleave mid-kick), then send-keys a single kick that starts reconstitution
# on its own. Backgrounded CHILD of this script (dies with the body); fires at most
# once. NAZIM_SELF_KICK=0 disables it (e.g. an operator attaching to drive by hand).
_RECON_KICK="[boot] Fresh console relaunch — begin your reconstitution NOW, do not wait for a nudge. Per the auto-injected reconstitution context: verify your model + token, read the newest reports/nazim-handoff-*.md IN FULL (its FINAL STATE block first) then CLAUDE.md, reconcile BOTH inboxes (operator_log.unprocessed() AND agent_messages to_agent='orch-console'), answer the operator ONLY via scripts/nazim_send.sh and stamp handled, then drive the board."
_self_kick() {
    # This runs in a backgrounded subshell that inherited the main shell's
    # _console_boot_exit EXIT trap (marks the console OFFLINE). Clear it FIRST — when
    # THIS subshell exits (right after firing) we must NOT mark the live console
    # offline; only fire_window's own release should run on our exit.
    trap - EXIT
    local tm pane i sess
    tm="$(command -v tmux || true)"; [ -x "$tm" ] || tm=/usr/local/bin/tmux
    sess="${ORCH_TMUX_SESSION:-nazim}"
    pane="${sess}:0.0"
    # Wait up to ~50s for claude's welcome banner to paint (it is then ready for input).
    for i in $(seq 1 25); do
        sleep 2
        "$tm" capture-pane -t "$pane" -p 2>/dev/null | grep -qE 'Claude Code v[0-9]' && break
    done
    if ! "$tm" capture-pane -t "$pane" -p 2>/dev/null | grep -qE 'Claude Code v[0-9]'; then
        echo "[boot_nazim] self-kick: banner never appeared within ~50s — NOT firing (claude may not be up)" >&2
        return 0
    fi
    # Take the host-wide fire-window lock the nudgers consult, so none can send-keys
    # into the pane between our text and its Enter. Self-expiring (TTL) + released on
    # this subshell's EXIT — a crash can never leave the pane quiesced.
    if [ -r "$ORCH_DIR/scripts/lib/fire_window.sh" ]; then
        . "$ORCH_DIR/scripts/lib/fire_window.sh" 2>/dev/null || true
        declare -f fire_window_hold >/dev/null 2>&1 && fire_window_hold "$sess" 60 "boot_nazim self-kick" 2>/dev/null || true
    fi
    # Reuse the fleet's ONE busy definition: if an external nudge already started a
    # turn (before our lock), the pane is BUSY -> skip; it is already reconstituting,
    # and we must not send-keys into a live turn.
    if [ -r "$ORCH_DIR/scripts/lib/composer_capture.sh" ]; then
        . "$ORCH_DIR/scripts/lib/composer_capture.sh" 2>/dev/null || true
        if declare -f pane_busy >/dev/null 2>&1; then
            pane_busy "$tm" "$pane"
            if [ "${CC_BUSY:-0}" = 1 ]; then
                echo "[boot_nazim] self-kick: pane already BUSY (${CC_BUSY_REASON:-turn in progress}) — a turn is already running, not double-firing" >&2
                return 0
            fi
        fi
    fi
    "$tm" send-keys -t "$pane" -l "$_RECON_KICK"
    sleep 1
    "$tm" send-keys -t "$pane" Enter
    echo "[boot_nazim] self-kick fired — fresh body reconstituting without waiting for an external nudge"
}
if [ "${NAZIM_SELF_KICK:-1}" = 1 ]; then _self_kick & fi

echo "[boot_nazim] $(date '+%H:%M:%S') launching $CLAUDE_BIN as ${ORCH_AGENT_ID:-orch-console} (body=${ORCH_BODY_ROLE:-?}, session=${ORCH_TMUX_SESSION:-?}, model=$NAZIM_MODEL)"
# FOREGROUND (was `exec`): this shell must survive claude to own the heartbeat loop and
# fire the EXIT trap (dead-man's-switch). When claude exits, the trap stops the heartbeat
# + marks offline, this script returns, the pane command ends, the tmux session closes,
# and the launchd waiter (boot_nazim_session.sh) restarts Nazim — same restart semantics
# as before, just with a bash parent owning the liveness loop.
"$CLAUDE_BIN" --dangerously-skip-permissions --model "$NAZIM_MODEL" "$@"
