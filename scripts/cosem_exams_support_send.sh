#!/usr/bin/env bash
# cosem_exams_support_send.sh — send a message to the cosem-exams client channel,
# sibling to scripts/cosem_tdu_support_send.sh / scripts/irsyad_support_send.sh.
#
# Channel shape (bot_channels channel_key='cosem-exams'): group_routing
# agent_phase='direct', agent_reviewer='orch-console' — i.e. the CONSOLE (Nazim)
# is the designated sender/reviewer for this client channel (cosem answers route
# to Hariz). Reads COSEM_EXAMS_BOT_TOKEN from .env. NEVER echoes the token.
#
# NOT gated by console_irsyad_client_send_gate.sh: that gate is op#21944's
# irsyad-specific console/execution key-separation control and does not extend to
# cosem. Generic anti-leak / arg-swap guards still apply.
#
# Usage:
#   scripts/cosem_exams_support_send.sh "message text" [tag]
#   echo "message" | scripts/cosem_exams_support_send.sh
#   TG_CHAT_OVERRIDE=<chat_id> scripts/cosem_exams_support_send.sh "message text"
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^COSEM_EXAMS_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
# chat: explicit override > COSEM_EXAMS_CHAT_ID in .env > registered allowed_chat_id (bot_channels)
CHAT="${TG_CHAT_OVERRIDE:-}"
if [ -z "${CHAT:-}" ]; then CHAT=$(grep '^COSEM_EXAMS_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2- || true); fi
if [ -z "${CHAT:-}" ]; then CHAT="-5390372474"; fi  # cosem-exams group, bot_channels allowed_chat_ids
[ -n "${TOK:-}" ] || { echo "COSEM_EXAMS_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "cosem-exams chat id missing (no override / .env / default)" >&2; exit 1; }

TEXT="${1:-$(cat)}"
TAG="${2:-cosem-exams}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }
# Fail loud on the channel-first arg-swap footgun (op#16353, same guard as the siblings).
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$TEXT" || exit 2
_send_tag_shape_guard "$TAG" || exit 2

# op#21145 (Musa direct): no internal identity / escalation framing reaches a client group.
source "$ORCH_DIR/scripts/lib/client_send_leak_guard.sh"
_client_send_leak_guard "$TEXT" || exit 3

# 2026-10-07 (orch-console #57970): refuse a weekday paired with the wrong date. Fail-closed.
source "$ORCH_DIR/scripts/lib/date_weekday_guard.sh"
_date_weekday_guard "$TEXT" || exit 6

# Send (chunked at Telegram's 4096-char limit). token/chat/text via env, never argv.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
# durable log every reply (CAI-598: log DELIVERY, not intent).
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" --tag "$TAG" $([ "$sent" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "cosem_exams_support_send failed" >&2; exit 1; }
