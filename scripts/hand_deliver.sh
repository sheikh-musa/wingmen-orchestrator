#!/usr/bin/env bash
# hand_deliver.sh — deliver a nudge to a lane whose composer holds a GHOST, when lane_nudge REFUSES.
#
# WHY THIS EXISTS. `lane_nudge.sh` refuses to type into a pane whose composer holds text, because
# clobbering a lane's real staged step is worse than not delivering. Correct. But the #23536
# phantom-text defect puts text there that NOBODY TYPED, and the refusal then strands the body:
# on 2026-08-16 SEVEN bodies at once (5 irsyad lanes, cc-quality, cc-fleet-health) were unreachable
# by the normal nudge path, silently. Every message to them landed on a closed door.
#
# This is the sanctioned manual fallback, and the ONLY thing that makes it safe is that it PROVES
# the ghost was never content before it commits: it types over the text and then checks the composer
# holds ONLY the new text. Real staged text cannot vanish when you type after it. If ANYTHING of the
# old text survives, it was real — so this backspaces out and refuses rather than clobbering it.
#
# It is a FALLBACK, not a replacement. Reach for lane_nudge first; come here only on its refusal.
# When cc-fleet-health's step-4 probe lands, lane_nudge should classify the ghost itself and this
# becomes redundant — delete it then rather than letting it linger as a second way to do one thing.
#
# TWO PREDICATE TRAPS, both of which failed me CLOSED before I got this right (2 wasted rounds):
#   1. The captured composer line is TRUNCATED to pane width. Test that the capture is a PREFIX of
#      the message, NEVER that it equals it.
#   2. The prompt is '❯' + U+00A0 NON-BREAKING SPACE, not a plain space. A `sed 's/^❯ *//'` leaves
#      the NBSP behind and every comparison then fails by one invisible character.
# Keep the message SHORT (< pane width) so the prefix check is meaningful.
#
# Usage:  scripts/hand_deliver.sh <tmux-session> "<short message>"
# Exit:   0 delivered · 3 composer not dim (may be REAL — refused) · 4 no fire_window hold
#         5 old text survived (was REAL — backed out) · 6 composer not a prefix of msg (backed out)
set -uo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"; cd "$ORCH_DIR"
. "$ORCH_DIR/scripts/lib/fire_window.sh"
SESS="$1"; MSG="$2"
PANE="$SESS"

before_raw=$(tmux capture-pane -e -p -t "$PANE" -S -6 | cat -v | grep -F 'M-bM-^]M-/' | tail -1)
before_txt=$(tmux capture-pane -p -t "$PANE" -S -6 | grep '^❯' | tail -1 | perl -CSD -pe 's/^\x{276f}//; s/\x{a0}/ /g; s/^\s+//; s/\s+$//')
case "$before_raw" in *'^[[2m'*) verdict="DIM(ghost)";; *) verdict="NOT-DIM";; esac
echo "  BEFORE: [$verdict] '$before_txt'"

if [ "$verdict" != "DIM(ghost)" ]; then
  echo "  ABORT — composer is NOT dim; that may be REAL staged text. Not typing over it." >&2; exit 3
fi

fire_window_hold "$SESS" 120 "orch-console hand-delivery past ghost" || { echo "  ABORT — could not take fire_window hold" >&2; exit 4; }

tmux send-keys -t "$PANE" -l "$MSG"
sleep 1.2
after_txt=$(tmux capture-pane -p -t "$PANE" -S -6 | grep '^❯' | tail -1 | perl -CSD -pe 's/^\x{276f}//; s/\x{a0}/ /g; s/^\s+//; s/\s+$//')
echo "  TYPED : '$(echo "$after_txt" | cut -c1-72)'"

# PROOF the ghost was never content: composer must now hold ONLY my text.
# The captured composer line is TRUNCATED to pane width, so `after == MSG` is the wrong
# test (it failed closed on every lane first time round). Correct test, both halves:
#   (a) the ghost text must be GONE  -> proves it was never content
#   (b) what IS there must be a PREFIX of my message -> proves nothing else crept in
if printf '%s' "$after_txt" | grep -qF -- "$before_txt"; then
  echo "  ABORT — old text SURVIVED alongside mine; that was REAL. Backspacing out." >&2
  tmux send-keys -t "$PANE" -N 400 BSpace; exit 5
fi
case "$MSG" in
  "$after_txt"*) echo "  VERIFY: ghost GONE + composer is a clean prefix of my text -> ghost was never content";;
  *) echo "  ABORT — composer is not a prefix of my message; unsafe. Backspacing out." >&2
     tmux send-keys -t "$PANE" -N 400 BSpace; exit 6;;
esac

tmux send-keys -t "$PANE" Enter
sleep 2
post=$(tmux capture-pane -p -t "$PANE" -S -6 | grep '^❯' | tail -1 | perl -CSD -pe 's/^\x{276f}//; s/\x{a0}/ /g; s/^\s+//; s/\s+$//')
if [ -z "$post" ]; then echo "  SUBMITTED: composer empty after Enter"
else echo "  ⚠ NOT SUBMITTED — composer still holds: '$(echo "$post" | cut -c1-60)'" >&2; fi
