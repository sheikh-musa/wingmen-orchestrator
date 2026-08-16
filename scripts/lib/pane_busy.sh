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

# THREE-COPY COLLAPSE (#23730/#23733). This used to key on 'esc to interrupt' only, so a lane
# in the EXTENDED-THINKING phase (a col-0 '(… thinking)' spinner, NO 'esc to interrupt') read
# IDLE — the answer that ENDS a lane (lane_winddown delegates here via live_is_busy). Rather
# than carry a THIRD copy of the busy patterns, delegate to the ONE hardened definition in
# composer_capture.sh. Named explicitly so it can never no-op onto another naive copy.
_PB_CC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/composer_capture.sh"

# Exit 0 (BUSY). An EMPTY capture is BUSY: fail closed, a pane we cannot see is not one we have
# proven safe to clear. Busy patterns (esc-to-interrupt + background-agents + thinking spinner)
# come from composer_capture.sh:_cc_text_busy. Pure text — no liveness (that is pane_busy below).
pane_busy_from_text() {
  local cap; cap="$(cat)"
  printf '%s' "$cap" | grep -q '[^[:space:]]' || return 0
  ( . "$_PB_CC"; _cc_text_busy "$cap" )
}

# Live check: delegate to composer_capture.sh's hardened pane_busy — thinking + background-agents
# + the op#11774 LIVENESS gate (a FROZEN spinner is STALE, so a dead 'thinking' pane can still be
# recycled rather than blocking self_recycle to its MAX_WAIT). Subshell so composer_capture's
# pane_busy(<tmux> <pane>) shadows this one only inside the delegation (no recursion).
pane_busy() {
  local sess="${1:?pane_busy <session>}" tm="${TMUX_BIN:-tmux}" cap
  cap="$("$tm" capture-pane -t "=$sess:0.0" -p 2>/dev/null)"
  printf '%s' "$cap" | grep -q '[^[:space:]]' || return 0   # empty/unreadable = busy (fail closed)
  ( . "$_PB_CC"; pane_busy "$tm" "=$sess:0.0"; [ "${CC_BUSY:-0}" = 1 ] )
}
