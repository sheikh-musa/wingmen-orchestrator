#!/usr/bin/env bash
# Minimal tmux STUB for testing scripts/reset_*.sh self-fire guards WITHOUT
# touching any real tmux session. Injected via the reset scripts' TM override.
# Controlled by env:
#   STUB_CALLER_SESS   what `display-message -p -t <pane> '#S'` returns (the
#                      session the *caller* is in — set == target to simulate a
#                      self-fire, != target to simulate an external fire)
#   STUB_SENDKEYS_LOG  file that `send-keys` appends to (proves keys WERE sent;
#                      must stay empty when a self-fire is correctly refused)
cmd="${1:-}"
case "$cmd" in
  has-session)   exit 0 ;;                                   # session "exists"
  display-message) echo "${STUB_CALLER_SESS:-}"; exit 0 ;;   # '#S' -> caller session
  capture-pane)  printf '\n'; exit 0 ;;                      # empty composer render
  send-keys)     [ -n "${STUB_SENDKEYS_LOG:-}" ] && printf '%s\n' "$*" >> "$STUB_SENDKEYS_LOG"; exit 0 ;;
  *)             exit 0 ;;
esac
