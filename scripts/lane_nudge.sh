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

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Shared, SGR-aware composer extraction — the fleet's ONE definition of "dim ghost
# vs real staged text" (reset_lane.sh sources the same lib; lane_wedge_watchdog.py
# mirrors its dim test). Used by the ghost-aware guard below so this wrapper never
# clobbers a lane's own genuinely-staged next-step, while still treating a dim
# autosuggestion ghost as the empty buffer it really is.
. "$ORCH_DIR/scripts/lib/composer_capture.sh" || { echo "lane_nudge: composer_capture.sh missing" >&2; exit 2; }

SESSION="${1:?usage: lane_nudge.sh <tmux-session> \"<message>\"}"
MSG="${2:?missing message}"
MAX_TRIES="${LANE_NUDGE_TRIES:-3}"

tmux has-session -t "$SESSION" 2>/dev/null || { echo "lane_nudge: no tmux session '$SESSION'" >&2; exit 2; }

# GHOST-AWARE COMPOSER GUARD (2026-07-29). The retry loop below CLEARS the composer
# (C-u ×2) before retyping — which would DESTROY any genuinely-staged next-step the
# lane typed for itself (a clobber-real-input violation). An IDLE Claude-Code lane
# also paints its most-recent history entry as a DIM (SGR-2) autosuggestion GHOST
# into an EMPTY input buffer; a plain capture-pane misreads that ghost as staged
# text. So read the composer with the shared SGR-aware extractor and REFUSE only
# when we POSITIVELY read REAL, non-dim staged text — preserving it — rather than
# clobber it. Empty / dim-ghost / placeholder / unreadable(noprompt) all PROCEED,
# exactly as before (so a fresh-boot pane, e.g. spawn_reviewer, is never blocked).
composer_parse_pane tmux "$SESSION"
if [ "${CC_EMPTY:-0}" != 1 ] && [ "${CC_PARTIAL:-noprompt}" != 'noprompt' ] && [ "${CC_N:-0}" -gt 0 ] 2>/dev/null; then
  mkdir -p "$ORCH_DIR/logs" 2>/dev/null || true
  printf '%s lane_nudge[%s] REFUSED, preserved staged: %s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SESSION" "$CC_FLAT" \
    >> "$ORCH_DIR/logs/lane_nudge_preserved_input.log" 2>/dev/null || true
  echo "lane_nudge: REFUSED — '$SESSION' composer holds REAL unsent text; clearing+retyping would clobber the lane's own staged step." >&2
  echo "           Preserved verbatim to logs/lane_nudge_preserved_input.log — submit/escalate it by hand rather than nudging over it." >&2
  exit 3
fi

pane_working() {
  # working iff the live footer shows the interrupt hint and NOT the idle hint
  local cap; cap="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -3)"
  printf '%s' "$cap" | grep -q 'esc to interrupt' && ! printf '%s' "$cap" | grep -q 'for agents'
}

pane_queued() {
  # A BUSY lane accepts the nudge into its message QUEUE — the TUI shows "Press up to edit
  # queued messages". That is a SUCCESSFUL delivery, but pane_working() can't see it (the
  # footer shows both hints at once), so the retry loop used to retype twice more and leave
  # three identical queued nudges behind. Observed 2026-07-25 on cosem-port, 34 min into a
  # task: one nudge became three. Delivery is the goal; queued IS delivered.
  tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -6 | grep -q 'queued message'
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
  if pane_queued; then
    echo "lane_nudge: '$SESSION' is BUSY — nudge accepted into its queue (try $try). Delivered; it reads at its next pause."
    exit 0
  fi
  # one extra Enter in case the TUI consumed the first as focus
  tmux send-keys -t "$SESSION" Enter; sleep 3
  if pane_working; then
    echo "lane_nudge: '$SESSION' submitted + working (try $try, 2nd Enter)"; exit 0
  fi
  if pane_queued; then
    echo "lane_nudge: '$SESSION' is BUSY — nudge accepted into its queue (try $try, 2nd Enter)."
    exit 0
  fi
  echo "lane_nudge: '$SESSION' not yet working after try $try — retrying" >&2
done

echo "lane_nudge: FAILED to verify submission to '$SESSION' after $MAX_TRIES tries — escalate (lane may be at a dialog/trust-prompt, or crashed)" >&2
exit 3
