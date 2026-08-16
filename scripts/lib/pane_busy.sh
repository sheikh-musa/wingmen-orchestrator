#!/usr/bin/env bash
# pane_busy.sh — is this pane busy RIGHT NOW? Read the live footer, never the scrollback.
#
# WHY THIS IS ITS OWN FILE. self_recycle's wait-for-idle loop originally asked
# `capture-pane -p | grep -q "esc to interrupt"`, which greps the WHOLE visible pane. A body
# that had been busy at any point still in the buffer therefore read as busy forever: cai sat
# at 838k with an empty composer while the waiter she fired refused to fire, holding a
# fire-window lock that also suppressed her wakes. It failed in the direction that LOOKS safe
# — it never clears a working body, it just never clears anything — which is how it survived
# review.
#
# The live footer is the only part of a pane that describes NOW; everything above it describes
# the past. lane_nudge.sh already read it correctly and the loop did not reuse it, so the rule
# now lives in one place with tests (tests/test_pane_busy.py).
#
# Usage:
#   . scripts/lib/pane_busy.sh
#   pane_busy <session>        # exit 0 = BUSY, 1 = idle
#   <capture> | pane_busy_from_text

# Exit 0 (BUSY) when the last non-blank lines show the interrupt hint and NOT the idle hint.
# An EMPTY capture is BUSY: fail closed, because a pane we cannot see is not a pane we have
# proven safe to clear.
pane_busy_from_text() {
  local cap; cap="$(grep -v '^[[:space:]]*$' | tail -3)"
  [ -n "$cap" ] || return 0
  printf '%s' "$cap" | grep -q 'esc to interrupt' \
    && ! printf '%s' "$cap" | grep -q 'for agents'
}

pane_busy() {
  local sess="${1:?pane_busy <session>}"
  local tm="${TMUX_BIN:-tmux}"
  "$tm" capture-pane -t "=$sess:0.0" -p 2>/dev/null | pane_busy_from_text
}
