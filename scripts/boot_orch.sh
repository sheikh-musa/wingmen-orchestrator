#!/usr/bin/env bash
# boot_orch.sh — Create the orch tmux session and block until it exits.
#
# launchd uses this with KeepAlive=true: when the orch session dies (crash,
# explicit kill, reboot), launchd re-runs this script which kills any stale
# session and starts a fresh one. This ensures the orch bridge is always live.
#
# Run manually:   scripts/boot_orch.sh
# Run in tmux:    tmux new-session -s orch -c ~/wingmen/orchestrator scripts/boot_orch.sh

set -euo pipefail

ORCH_DIR="$HOME/wingmen/orchestrator"
# Resolve claude robustly: under launchd (and non-login SSH) PATH is minimal, so
# `command -v claude` can miss a per-user install (the Mini keeps it at
# ~/.local/bin/claude). Fall back through the known install locations.
CLAUDE_BIN="$(command -v claude || true)"
if [[ -z "$CLAUDE_BIN" ]]; then
    for _c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
        [[ -x "$_c" ]] && CLAUDE_BIN="$_c" && break
    done
fi
# Resolve tmux the same way: under launchd's minimal PATH bare `tmux` is NOT
# found (the Studio keeps it at /opt/homebrew/bin/tmux) — an unresolved `tmux`
# makes the whole boot fail silently, so the KeepAlive job can never bring the
# hub back. Fall back through the known install locations. (2026-07-08: this was
# exactly why the Studio hub could not self-restart.)
TMUX_BIN="$(command -v tmux || true)"
if [[ -z "$TMUX_BIN" ]]; then
    for _t in /opt/homebrew/bin/tmux /usr/local/bin/tmux "$HOME/.local/bin/tmux"; do
        [[ -x "$_t" ]] && TMUX_BIN="$_t" && break
    done
fi
[[ -n "$TMUX_BIN" ]]  || { echo "[boot_orch] FATAL: tmux not found" >&2; exit 1; }
[[ -n "$CLAUDE_BIN" ]] || { echo "[boot_orch] FATAL: claude not found" >&2; exit 1; }
SLEEP_BETWEEN_RESTARTS=5

log() { echo "[boot_orch] $(date '+%H:%M:%S') $*"; }

# Load env so CLAUDE_CODE_OAUTH_TOKEN is available in the tmux session
if [[ -f "$ORCH_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ORCH_DIR/.env"
    set +a
fi

# Force the Mac Mini's Claude Max subscription, never metered API billing.
# .env carries a live ANTHROPIC_API_KEY; if it survives into the environment,
# `claude` silently routes this session — the single most continuously-running
# body in the fleet — through the paid API instead of Max (MEMORY: "Lane .env
# ANTHROPIC_API_KEY forces METERED API"). Every sibling launcher (boot_nazim.sh,
# boot_cai.sh, boot_fleet_health.sh, launch_dangerous_cc.sh) already strips it
# here; boot_orch.sh was the lone omission (fable substrate scan, critic #2).
# Keep CLAUDE_CODE_OAUTH_TOKEN — tmux/headless can't read the GUI-login OAuth
# from the Keychain and needs it for auth.
unset ANTHROPIC_API_KEY

# Fleet donor-account default (token conservation, op#7994): if a
# .orch_default_token POINTER file (holding the PATH to a 0600 token file) is
# present, boot the hub on that account (Syed) instead of the .env default
# (Musa) — so a launchd/KeepAlive restart STAYS on the account the operator
# moved the hub to, instead of silently reverting to .env. Fail-open: an
# unreadable/stale pointer path is skipped -> .env account. Mirrors
# launch_dangerous_cc.sh's lane-default knob (pointer holds a PATH only, so no
# secret enters the repo). Applied AFTER the .env source so it wins.
if [ -r "$ORCH_DIR/.orch_default_token" ]; then
    _ORCH_DEFAULT_TOKFILE="$(tr -d '[:space:]' < "$ORCH_DIR/.orch_default_token")"
    if [ -n "$_ORCH_DEFAULT_TOKFILE" ] && [ -r "$_ORCH_DEFAULT_TOKFILE" ]; then
        CLAUDE_CODE_OAUTH_TOKEN="$(cat "$_ORCH_DEFAULT_TOKFILE")"
        export CLAUDE_CODE_OAUTH_TOKEN
        log "hub-default OAuth account applied (pointer .orch_default_token -> $_ORCH_DEFAULT_TOKFILE)"
    fi
fi

# ORCH-TOPOLOGY-001: session name is body-scoped. The hub boots as `orch`
# (bridge exact-matches =orch); the console body (Nazim, operator's MacBook)
# sets ORCH_TMUX_SESSION=nazim in .env so a non-hub session NEVER claims the
# hub's name (the leftover `orch` name is how the 07-04 pen-(iv) slip happened).
SESSION="${ORCH_TMUX_SESSION:-orch}"

# ADOPT an existing live session — do NOT kill it. If `orch` already exists
# (an operator/break-glass manual bring-up, or a still-running prior instance),
# just supervise it. This lets the launchd KeepAlive job be (re)loaded WITHOUT
# interrupting a live hub mid-task — the reason a naive `launchctl bootstrap`
# used to blip the operator's session. A fresh session is created only when none
# exists; KeepAlive re-runs this script after the session actually dies.
if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
    log "adopting existing live '$SESSION' session (no restart)"
else
    log "starting orch session (claude --continue in tmux '$SESSION')"
    # --continue (NOT --resume): --resume opens an interactive session PICKER
    # that, unattended in a detached tmux, hangs at the menu instead of coming
    # up live — so a launchd/manual restart would leave the hub stuck at a
    # chooser. --continue auto-resumes the most-recent conversation in this dir
    # (the hub's own ongoing session) → an unattended restart returns LIVE with
    # context.
    "$TMUX_BIN" new-session -d -s "$SESSION" -c "$ORCH_DIR" -e "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-}" \
        -- "$CLAUDE_BIN" --dangerously-skip-permissions --continue --model "${ORCH_MODEL:-claude-opus-4-8}"
fi

# Self-register the hub's tmux session in agent_status so the realtime auto-wake
# path (agent_wake.resolve_tmux_session, DB-first) can deliver a wake to us. The
# hub boots via `claude --continue` (NOT launch_dangerous_cc.sh), so it was the
# lone body that never registered — leaving resolve_tmux_session('cc-orchestrator')
# = None, so every correctly-flagged P1 wake no-oped with 'no live session'
# (root cause of cc-fleet-health #16412; the fallback cwd-token path also misses
# because the hub's cwd '.../wingmen/orchestrator' doesn't contain the
# 'wingmen-orchestrator' repo_scope token). Best-effort: a DB blip must not block
# the boot. The write goes through the hardened identity trigger, so we SET the
# app.current_agent_id GUC in-txn (mirrors launch_dangerous_cc.sh).
if [[ -x "$ORCH_DIR/.venv/bin/python3" ]]; then
    "$ORCH_DIR/.venv/bin/python3" - "$SESSION" <<'PYREG' || log "agent_status self-register skipped (best-effort)"
import os, sys, socket, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
sess = (sys.argv[1] if len(sys.argv) > 1 else "orch").strip()
if not dsn:
    sys.exit(0)
conn = psycopg.connect(dsn)
try:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", ("cc-orchestrator",))
        cur.execute(
            "INSERT INTO agent_status "
            "(agent_id, base_agent_id, status, current_task, scope_repos, tmux_session, host, last_heartbeat, updated_at) "
            "VALUES ('cc-orchestrator','cc-orchestrator','working','hub — always-on orchestrator', "
            "ARRAY['wingmen-orchestrator']::text[], NULLIF(%s,''), NULLIF(%s,''), now(), now()) "
            "ON CONFLICT (agent_id) DO UPDATE SET status='working', "
            "tmux_session=NULLIF(%s,''), host=NULLIF(%s,''), last_heartbeat=now(), updated_at=now()",
            (sess, socket.gethostname(), sess, socket.gethostname()))
    conn.commit()
finally:
    conn.close()
print("agent_status: cc-orchestrator registered on session", sess)
PYREG
fi

log "session '$SESSION' is live — waiting for it to exit"

# ── Periodic heartbeat + dead-man's-switch (op#11565 f/u, CAI-RESP-807) ──────
# The hub had NO periodic heartbeat writer: the registration above stamps
# last_heartbeat only at (re)start, so a hub running for days shows a chronically
# stale heartbeat and the 2h agent_watchdog offline-flip never fires — a dead
# liveness signal on the always-on hub (found ~3.3d stale). This supervisor loop
# runs ONLY while the session is alive, so refreshing last_heartbeat inside it IS
# the dead-man's-switch: when the hub dies the loop exits and the ticks stop.
# The write MUST set the identity GUC in the SAME txn as the UPDATE (hardened
# identity trigger, BUG-024/ARCH-035) — mirrors the console fix (boot_nazim cdc9131).
HB_EVERY_SEC="${ORCH_HB_EVERY_SEC:-300}"
_hub_db() {  # $1 = beat | offline — best-effort, never breaks the boot
    [[ -x "$ORCH_DIR/.venv/bin/python3" ]] || return 0
    "$ORCH_DIR/.venv/bin/python3" - "${1:-beat}" <<'PYHB' 2>/dev/null || true
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if not dsn:
    sys.exit(0)
mode = sys.argv[1] if len(sys.argv) > 1 else "beat"
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
    if mode == "offline":
        cur.execute("UPDATE agent_status SET status='offline', last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-orchestrator'")
    else:
        cur.execute("UPDATE agent_status SET last_heartbeat=now(), updated_at=now() WHERE agent_id='cc-orchestrator'")
    conn.commit()
PYHB
}
# Clean-exit offline marker: when the session dies the loop exits and this fires,
# marking the hub offline until launchd's KeepAlive re-runs boot_orch (which re-
# registers 'working'). NOTE (#17025): the watchdog's live-session carve-out still
# governs PAGING (it gates on a live tmux session, not this row), so the transient
# offline across a restart does not itself page; the fresh boot re-asserts 'working'.
trap '_hub_db offline' EXIT

# Block until session ends; launchd's KeepAlive will restart us when we exit.
# Refresh the heartbeat every HB_EVERY_SEC while the session lives (dead-man's-switch).
_hb_last=$(date -u +%s)
while "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; do
    sleep "$SLEEP_BETWEEN_RESTARTS"
    _now=$(date -u +%s)
    if [ $(( _now - _hb_last )) -ge "$HB_EVERY_SEC" ]; then
        _hub_db beat
        _hb_last=$_now
    fi
done

log "session '$SESSION' ended — launchd will restart shortly"
