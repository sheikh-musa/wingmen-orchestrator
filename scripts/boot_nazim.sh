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

# Max-subscription billing: scrub the metered API key (its mere presence flips
# `claude` to API billing); keep CLAUDE_CODE_OAUTH_TOKEN (tmux/headless can't read
# the GUI-login OAuth from the Keychain).
unset ANTHROPIC_API_KEY

# Resolve claude robustly — the Mini keeps it at ~/.local/bin, off the non-login PATH.
CLAUDE_BIN="$(command -v claude || true)"
if [[ -z "$CLAUDE_BIN" ]]; then
    for _c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
        [[ -x "$_c" ]] && CLAUDE_BIN="$_c" && break
    done
fi
[[ -n "$CLAUDE_BIN" ]] || { echo "[boot_nazim] claude binary not found" >&2; exit 1; }

# Model is .env-driven (NAZIM_MODEL) so the launchd KeepAlive waiter's no-arg
# auto-restart honors the operator's choice too — the old hardcoded flag pinned
# every auto-restart to 4.8 and left the operator no way in without racing the
# waiter (op#7004). Default = Opus 4.8 (parity with the hub orch + cai). A caller
# --model in "$@" still comes later on argv and wins (claude last-wins parsing).
NAZIM_MODEL="${NAZIM_MODEL:-claude-opus-4-8}"
echo "[boot_nazim] $(date '+%H:%M:%S') launching $CLAUDE_BIN as ${ORCH_AGENT_ID:-orch-console} (body=${ORCH_BODY_ROLE:-?}, session=${ORCH_TMUX_SESSION:-?}, model=$NAZIM_MODEL)"
exec "$CLAUDE_BIN" --dangerously-skip-permissions --model "$NAZIM_MODEL" "$@"
