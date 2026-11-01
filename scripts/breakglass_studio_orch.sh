#!/usr/bin/env bash
# breakglass_studio_orch.sh — Nazim (Mini) → revive a dead Studio hub over SSH.
#
# The manual backstop for when the Studio orch (cc-orchestrator, tmux `orch`,
# ORCH_BODY_ROLE=hub) is down and the operator can't restart it from the Studio
# itself (launchd KeepAlive not loaded / --resume picker hang, both seen
# 2026-07-08). Restoration of the hub's OWN session on the hub's OWN machine —
# NOT an orch_lease takeover; the returning session boots AS the hub.
#
# Runs from the Mini (Nazim's body). Idempotent + safe:
#   - if `orch` is already live on the Studio, it reports and exits 0 (no kill)
#     unless --force is given.
#   - otherwise it (re)creates the `orch` session with `claude --continue` so the
#     hub returns LIVE with its context, then captures the pane as proof.
#
# Usage:  scripts/breakglass_studio_orch.sh [--force]
set -euo pipefail

STUDIO="${STUDIO_SSH_HOST:-Musa@mac-studio}"
FORCE="${1:-}"

echo "[breakglass] target hub host: $STUDIO"

# The Studio's non-login SSH shell has a minimal PATH: tmux at /opt/homebrew/bin,
# claude at ~/.local/bin. Everything below runs remotely in one heredoc so a
# dropped SSH mid-way can't leave a half-started session.
ssh -o BatchMode=yes -o ConnectTimeout=10 "$STUDIO" FORCE="$FORCE" 'bash -s' <<'REMOTE'
set -euo pipefail
cd "$HOME/wingmen/orchestrator" || { echo "[remote] orch dir missing" >&2; exit 9; }
set -a; source .env; set +a
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:$PATH"

TMUX_BIN="$(command -v tmux || true)"; [ -n "$TMUX_BIN" ] || TMUX_BIN=/opt/homebrew/bin/tmux
CLAUDE_BIN="$HOME/.local/bin/claude"; [ -x "$CLAUDE_BIN" ] || CLAUDE_BIN="$(command -v claude)"
SESSION="${ORCH_TMUX_SESSION:-orch}"
ROLE="${ORCH_BODY_ROLE:-unset}"

# Guard: this machine MUST be the hub. Refuse to spawn `orch` on a console body.
if [ "$ROLE" != "hub" ]; then
  echo "[remote] REFUSING: ORCH_BODY_ROLE=$ROLE (not hub) — would create a rogue orch" >&2
  exit 4
fi

if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  if [ "${FORCE:-}" != "--force" ]; then
    echo "[remote] '$SESSION' already live on the hub — nothing to do (pass --force to recycle)."
    "$TMUX_BIN" capture-pane -t "$SESSION" -p 2>/dev/null | grep -vE '^\s*$' | tail -6
    exit 0
  fi
  echo "[remote] --force: recycling existing '$SESSION' session"
  "$TMUX_BIN" kill-session -t "$SESSION" || true
  sleep 2
fi

# Cross-provider resume gate (cc-substrate track B, 2026-11-01): `--continue` replays the
# newest transcript in this directory — the transcript that history carries. If that
# conversation last ran on a DIFFERENT provider and holds secret-shaped values, continuing
# exports them to the new one. Same gate as boot_orch.sh / launch_dangerous_cc.sh, and the
# same failure policy: on refusal the session still comes back, just FRESH (no old context)
# — a breakglass that refuses to boot at all would be worse than the leak it prevents.
_CONTINUE_FLAG="--continue"
if [ -r scripts/lib/session_provider_guard.sh ]; then
  # shellcheck source=scripts/lib/session_provider_guard.sh
  . scripts/lib/session_provider_guard.sh
  _SPG_RC=0
  _SPG_OUT="$(spg_check "${ORCH_MODEL:-claude-opus-4-8}" --continue --cwd "$PWD" 2>&1)" || _SPG_RC=$?
  if [ "$_SPG_RC" -ne 0 ]; then
    _CONTINUE_FLAG=""
    echo "[remote] REFUSING --continue (provider switch with a non-clean transcript) — booting FRESH"
    printf '%s\n' "$_SPG_OUT" | while IFS= read -r _l; do echo "[remote]   $_l"; done
  else
    echo "[remote] resume guard: ${_SPG_OUT%%$'\n'*}"
  fi
fi

echo "[remote] starting '$SESSION' (claude ${_CONTINUE_FLAG:-fresh}, role=$ROLE)"
"$TMUX_BIN" new-session -d -s "$SESSION" -c "$HOME/wingmen/orchestrator" \
  -e "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-}" \
  -- "$CLAUDE_BIN" --dangerously-skip-permissions $_CONTINUE_FLAG --model claude-opus-4-8
sleep 6

if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  echo "[remote] OK — '$SESSION' is live. Pane tail:"
  "$TMUX_BIN" capture-pane -t "$SESSION" -p 2>/dev/null | grep -vE '^\s*$' | tail -8
else
  echo "[remote] FAILED — '$SESSION' did not survive; check claude auth / logs" >&2
  exit 5
fi
REMOTE

echo "[breakglass] done. Operator can attach on the Studio: tmux attach -t orch"
