#!/usr/bin/env bash
# send_arg_guard.sh — shared guard against the send-helper arg-order footgun.
#
# WHY (op#16353, 2026-08-24): every fleet send helper is TEXT-FIRST —
# `<script> "<message>" [tag]`. Nazim once called irsyad_support_send.sh
# channel-first (`... gazzabyte-irsyad "<real message>"`), so the bot posted the
# literal channel name "gazzabyte-irsyad" into the client group THREE times while
# the real replies went into the unsent tag field — and the delivery log recorded
# delivered=True (for the wrong text), so the verification passed. This guard makes
# that exact mistake fail LOUD instead of silently shipping the tag as the message.
#
# Usage (source, then call with the resolved message text):
#   source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
#   _send_arg_guard "$TEXT" || exit 2
_send_arg_guard() {
  case "$1" in
    gazzabyte-irsyad|nazim-console|tmux-console|console|operator-orch|cosem-exams|hub|fleet-health|cc-orchestrator)
      echo "ERROR: first argument '$1' looks like a channel/tag, not a message body." >&2
      echo "Fleet send helpers are TEXT-FIRST:  <script> \"<message text>\" [tag]" >&2
      echo "You almost certainly swapped the args (channel-first). Aborting the send." >&2
      return 2 ;;
  esac
  return 0
}
