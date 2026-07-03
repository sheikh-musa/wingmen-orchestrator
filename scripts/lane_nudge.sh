#!/usr/bin/env bash
# lane_nudge.sh — VERIFIED-SUBMIT wrapper for nudging a CC lane's tmux session.
#
# WHY: `tmux send-keys ... Enter` is unreliable — the Enter frequently fails to
# submit, leaving the lane IDLE with the prompt sitting unsent in its input box.
# This silently stalled lanes ~5x on 2026-06-20 (fleet drifted idle while the
# operator was engaged elsewhere). cai CAI-RESP-284 HARD RULE 1: a lane auto-nudge
# MUST NOT depend on a bare send-keys Enter — use a verified submit (confirm it
# actually submitted; clear+retype fallback). This is that wrapper.
#
# Usage:  lane_nudge.sh <tmux-session> "<message>"
# Exit:   0 = verified submitted (pane entered a working state)
#         3 = could not verify submission after retries (caller should escalate)
#         2 = usage / no such session
#
# Verification heuristic (matches the observable Claude-Code TUI states):
#   working  -> footer shows "esc to interrupt"   (submitted, lane is running)
#   idle     -> footer shows "for agents"          (NOT submitted / still idle)
set -uo pipefail

SESSION="${1:?usage: lane_nudge.sh <tmux-session> \"<message>\"}"
MSG="${2:?missing message}"
MAX_TRIES="${LANE_NUDGE_TRIES:-3}"

tmux has-session -t "$SESSION" 2>/dev/null || { echo "lane_nudge: no tmux session '$SESSION'" >&2; exit 2; }

pane_working() {
  # working iff the live footer shows the interrupt hint and NOT the idle hint
  local cap; cap="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -3)"
  printf '%s' "$cap" | grep -q 'esc to interrupt' && ! printf '%s' "$cap" | grep -q 'for agents'
}

for try in $(seq 1 "$MAX_TRIES"); do
  # clear any stale/unsent input, then type fresh, then submit
  tmux send-keys -t "$SESSION" C-u; sleep 0.4
  tmux send-keys -t "$SESSION" C-u; sleep 0.4
  tmux send-keys -t "$SESSION" -l "$MSG"; sleep 1
  tmux send-keys -t "$SESSION" Enter; sleep 4
  if pane_working; then
    echo "lane_nudge: '$SESSION' submitted + working (try $try)"; exit 0
  fi
  # one extra Enter in case the TUI consumed the first as focus
  tmux send-keys -t "$SESSION" Enter; sleep 3
  if pane_working; then
    echo "lane_nudge: '$SESSION' submitted + working (try $try, 2nd Enter)"; exit 0
  fi
  echo "lane_nudge: '$SESSION' not yet working after try $try — retrying" >&2
done

echo "lane_nudge: FAILED to verify submission to '$SESSION' after $MAX_TRIES tries — escalate (lane may be at a dialog/trust-prompt, or crashed)" >&2
exit 3
