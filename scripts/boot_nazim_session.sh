#!/usr/bin/env bash
# boot_nazim_session.sh — launchd KeepAlive WAITER for the Nazim console session.
#
# Keeps the operator's always-on CTO (Nazim, ORCH-TOPOLOGY-001 console body) alive
# across crashes + reboots. It ADOPTS an existing healthy 'nazim' tmux session
# (NEVER kills a live one) and only starts one if absent, then blocks until the
# session exits — so launchd's KeepAlive re-runs this and restarts Nazim after a
# crash or boot. A fresh restart re-orients itself from CLAUDE.md + STATUS +
# memory + operator_log (the "Nazim survives machine loss" design); the first
# operator DM to @nazim_cto_bot nudges it via nazim-ingest and it reconciles.
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
[ -f "$ORCH_DIR/.env" ] && { set -a; . "$ORCH_DIR/.env"; set +a; }
SESSION="${ORCH_TMUX_SESSION:-nazim}"

TMUX_BIN="$(command -v tmux || true)"
if [[ -z "$TMUX_BIN" ]]; then
    for t in /opt/homebrew/bin/tmux /usr/local/bin/tmux; do [[ -x "$t" ]] && TMUX_BIN="$t" && break; done
fi
[[ -n "$TMUX_BIN" ]] || { echo "[boot_nazim_session] $(date '+%H:%M:%S') no tmux found" >&2; exit 1; }

if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
    echo "[boot_nazim_session] $(date '+%H:%M:%S') adopting live '$SESSION' session (no restart)"
else
    echo "[boot_nazim_session] $(date '+%H:%M:%S') no '$SESSION' session — starting a fresh one"
    # bash -lc gives the login PATH (node etc.); boot_nazim.sh resolves claude + cds.
    "$TMUX_BIN" new-session -d -s "$SESSION" -x 220 -y 50 -c "$ORCH_DIR" \
        "bash -lc 'exec $ORCH_DIR/scripts/boot_nazim.sh'"
fi

# Block until the session ends; launchd KeepAlive re-runs us to restart Nazim.
while "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; do sleep 5; done
echo "[boot_nazim_session] $(date '+%H:%M:%S') '$SESSION' ended — launchd will restart"
