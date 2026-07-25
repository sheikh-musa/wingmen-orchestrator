#!/usr/bin/env bash
# cai_send.sh — send a message to the operator via the cai bot (@cai_orch_bot).
# The outbound half of the cai<->operator 2-way bridge: this is how cai replies
# to the operator's phone (governance sign-offs, strategy, safety/authority calls).
#
# Reads CAI_TELEGRAM_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:
#   scripts/cai_send.sh "message text"
#   echo "message" | scripts/cai_send.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^CAI_TELEGRAM_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
# Default target = the operator (Musa). TG_CHAT_OVERRIDE lets the bridge route a
# reply to a different authorized chat.
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "CAI_TELEGRAM_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }

TEXT="${1:-$(cat)}"
TAG="${2:-cai-channel}"   # distinguishing tag for the cai channel
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }

# Send (chunked at Telegram's 4096-char limit so long replies aren't truncated).
# token/chat/text passed via env, never argv — keeps the token out of `ps`.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
# durable log every reply (full text, once; best-effort — never fail on a log hiccup).
# PYTHONPATH pins the package root so the `-m` import works regardless of CWD — cai
# runs this from ~/wingmen/wingmen-cai, where a bare `-m nervous_system.operator_log`
# would ModuleNotFoundError and silently drop the log (caught 2026-06-28: cai's
# war-room sends delivered but never logged because of exactly this).
# CAI-598: log DELIVERY, not intent. This log line used to run unconditionally with
# delivered defaulting to TRUE, so a failed send was recorded as delivered — the
# durable log asserted an outcome it never checked. Pass --undelivered on failure.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" --tag "$TAG" $([ "$sent" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "cai_send failed" >&2; exit 1; }
