#!/usr/bin/env bash
# nazim_model.sh — switch the model Nazim (orch-console) runs on, WITHOUT racing
# the launchd KeepAlive waiter (dev.wingmen.nazim-session).
#
# The problem this solves (op#7004): Nazim is always-on — the launchd waiter
# adopts/owns the 'nazim' tmux session and auto-restarts it via boot_nazim.sh with
# NO args, so a manual `tmux new-session -s nazim … --model X` either collides
# (duplicate session) or gets clobbered by the waiter's default-model restart.
#
# Fix: the model is now .env-driven (NAZIM_MODEL). This script rewrites that var
# and kills the session so the waiter relaunches Nazim on the new model — i.e. we
# let the auto-restart do exactly what the operator wanted, on the chosen model.
# A model swap is always a fresh process (context can't migrate across models);
# fresh Nazim reorients from the handoff + operator_log by design.
#
#   scripts/nazim_model.sh claude-opus-4-8    # or claude-sonnet-5, claude-haiku-4-5-20251001
#   scripts/nazim_model.sh                    # no arg → just print the current setting
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
ENV_FILE="$ORCH_DIR/.env"
SESS="nazim"
TM="$(command -v tmux || echo /opt/homebrew/bin/tmux)"

cur="$(grep -E '^NAZIM_MODEL=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
if [[ $# -eq 0 || -z "${1:-}" ]]; then
    echo "current NAZIM_MODEL=${cur:-<unset, defaults to claude-opus-4-8>}"
    echo "usage: $0 <model-id>   (e.g. claude-opus-4-8, claude-sonnet-5)"
    exit 0
fi
NEW="$1"

if [[ "$cur" == "$NEW" ]]; then
    echo "[nazim_model] NAZIM_MODEL already $NEW — restarting session to apply anyway"
else
    if grep -qE '^NAZIM_MODEL=' "$ENV_FILE"; then
        # portable in-place edit (BSD sed on macOS needs the '' arg)
        sed -i '' -E "s|^NAZIM_MODEL=.*|NAZIM_MODEL=${NEW}|" "$ENV_FILE"
    else
        printf 'NAZIM_MODEL=%s\n' "$NEW" >> "$ENV_FILE"
    fi
    echo "[nazim_model] NAZIM_MODEL: ${cur:-<unset>} -> $NEW"
fi

if "$TM" has-session -t "$SESS" 2>/dev/null; then
    echo "[nazim_model] killing '$SESS' — launchd KeepAlive will relaunch on $NEW ..."
    "$TM" kill-session -t "$SESS"
    echo "[nazim_model] done. Waiter restarts within ~ThrottleInterval (15s); fresh Nazim will state its model on boot."
else
    echo "[nazim_model] no live '$SESS' session; waiter will start one on $NEW at next tick."
fi
