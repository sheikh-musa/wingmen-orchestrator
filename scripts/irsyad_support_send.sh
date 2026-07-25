#!/usr/bin/env bash
# irsyad_support_send.sh — send a message to the Gazzabyte / Irsyad-Support group
# via the Irsyad-Support bot. The outbound half of the scoped PERIMETER bridge:
# this is how cc-orchestrator (the human-owned hub) replies into the external
# vendor (Gazzabyte) group — e.g. ticket-approval acks.
#
# SECURITY (perimeter): default target is the Gazzabyte group ONLY
# (IRSYAD_SUPPORT_GROUP_CHAT_ID). Reads IRSYAD_SUPPORT_BOT_TOKEN from .env.
# NEVER echoes the token.
# Usage:
#   scripts/irsyad_support_send.sh "message text"
#   echo "message" | scripts/irsyad_support_send.sh
#   TG_CHAT_OVERRIDE=<chat_id> scripts/irsyad_support_send.sh "message text"
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^IRSYAD_SUPPORT_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
# Default target = the Gazzabyte group. TG_CHAT_OVERRIDE lets the bridge route a
# reply to a different authorized chat.
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^IRSYAD_SUPPORT_GROUP_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "IRSYAD_SUPPORT_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "IRSYAD_SUPPORT_GROUP_CHAT_ID missing from .env (and no TG_CHAT_OVERRIDE)" >&2; exit 1; }

TEXT="${1:-$(cat)}"
TAG="${2:-gazzabyte-irsyad}"   # scoped tag for the Gazzabyte/Irsyad-Support channel
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
# PYTHONPATH pins the package root so the `-m` import works regardless of CWD.
# CAI-598: log DELIVERY, not intent. This log line used to run unconditionally with
# delivered defaulting to TRUE, so a failed send was recorded as delivered — the
# durable log asserted an outcome it never checked. Pass --undelivered on failure.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" --tag "$TAG" $([ "$sent" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "irsyad_support_send failed" >&2; exit 1; }
