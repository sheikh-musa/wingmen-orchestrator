#!/usr/bin/env bash
# angullia_send.sh — send a message to the angullia support channel,
# sibling to scripts/cosem_tdu_support_send.sh (op#21154/op#22433).
#
# Supervised shape: a reviewer (cc-angullia, per bot_channels.group_routing
# on channel_key='angullia') sends here — the raw builder lane never sends
# directly (though for angullia coord and builder are the same lane). Reads
# ANGULLIA_BOT_TOKEN from .env. NEVER echoes the token.
#
# NOT gated by console_irsyad_client_send_gate.sh: that gate is op#21944's
# irsyad-specific console/execution key-separation control and does not
# extend to angullia. Generic anti-leak / arg-swap guards still apply.
#
# Usage:
#   scripts/angullia_send.sh "message text"
#   echo "message" | scripts/angullia_send.sh
#   TG_CHAT_OVERRIDE=<chat_id> scripts/angullia_send.sh "message text"
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^ANGULLIA_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^ANGULLIA_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "ANGULLIA_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "ANGULLIA_CHAT_ID missing from .env (and no TG_CHAT_OVERRIDE)" >&2; exit 1; }

TEXT="${1:-$(cat)}"
TAG="${2:-angullia}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }
# Fail loud on the channel-first arg-swap footgun (op#16353, same guard as irsyad's).
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$TEXT" || exit 2

# op#21145 (Musa direct): no internal identity / escalation framing reaches a client group.
source "$ORCH_DIR/scripts/lib/client_send_leak_guard.sh"
_client_send_leak_guard "$TEXT" || exit 3

# Send (chunked at Telegram's 4096-char limit so long replies aren't truncated).
# token/chat/text passed via env, never argv — keeps the token out of `ps`.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
# durable log every reply (full text, once; best-effort — never fail on a log hiccup).
# CAI-598: log DELIVERY, not intent — --undelivered on a failed send.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" --tag "$TAG" $([ "$sent" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "angullia_send failed" >&2; exit 1; }
