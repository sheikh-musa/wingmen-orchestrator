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

# _send_tag_shape_guard — arg2-shape check (bus #44966/#44990/#45020).
#
# WHY: _send_arg_guard above only checks arg1 (TEXT) against a hardcoded
# allowlist of known channel names. It never validated arg2 (TAG)'s *shape* —
# so a caller landing prose (or a raw Telegram chat_id) in the tag slot, where
# that string isn't literally one of the hardcoded names, sailed straight
# through unguarded all the way to the operator_messages INSERT. That's
# exactly how op#16353's leaked drafts (ids 16347/16348/16352) and the
# 2026-06-30 chat_id-shaped tags (ids 1675/1758) got in. Mirrors the DB-layer
# CHECK (operator_messages.tag_shape_chk) and nervous_system/operator_log.py's
# _validate_tag_shape — same regex, same posture, checked here too so a bad
# tag fails at the shell before it even reaches Python/the DB.
_send_tag_shape_guard() {
  local tag="$1"
  [ -z "$tag" ] && return 0
  if [ "${#tag}" -gt 64 ] || ! [[ "$tag" =~ ^[a-zA-Z@][a-zA-Z0-9@_/+-]*$ ]]; then
    echo "ERROR: tag argument '$tag' doesn't look like a channel tag (<=64 chars, ^[a-zA-Z@][a-zA-Z0-9@_/+-]*\$)." >&2
    echo "This is the same op#16353 arg-slot footgun class — prose or an id landed in the tag slot." >&2
    echo "Aborting the send." >&2
    return 2
  fi
  return 0
}
