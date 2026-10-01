#!/usr/bin/env bash
# mamadah_send.sh — send a message to Mama Dah's Assistant's private family group,
# sibling to scripts/oeh_send.sh (bus #47808, op#24172/op#24173).
#
# Supervised shape: a reviewer (cc-mamadah, per bot_channels.group_routing
# on channel_key='mamadah') sends here — the raw builder lane never sends
# directly (though for mamadah coord and builder are the same lane). Reads
# MAMADAH_BOT_TOKEN from .env. NEVER echoes the token.
#
# This is PRIVATE FAMILY data (Musa + wife Zahidah), not a client workstream,
# but the same anti-leak / arg-swap guards apply — no internal fleet/ops
# framing belongs in that group either.
#
# Usage:
#   scripts/mamadah_send.sh "message text"
#   echo "message" | scripts/mamadah_send.sh
#   TG_CHAT_OVERRIDE=<chat_id> scripts/mamadah_send.sh "message text"
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^MAMADAH_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MAMADAH_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "MAMADAH_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MAMADAH_CHAT_ID missing from .env (and no TG_CHAT_OVERRIDE)" >&2; exit 1; }

# wingmen-personal credential for operator_log's personal-routing split-write
# (bus #47837 C3) — GUARDED file, deliberately NOT in .env. Scoped to this
# outbound-send process only, not exported fleet-wide.
export WINGMEN_PERSONAL_ALLOWED=1
. "$HOME/.wingmen/private/wingmen_personal.env"

TEXT="${1:-$(cat)}"
TAG="${2:-mamadah}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }
# Fail loud on the channel-first arg-swap footgun (op#16353, same guard as irsyad's).
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$TEXT" || exit 2
_send_tag_shape_guard "$TAG" || exit 2

# No internal identity / escalation framing reaches the family group (same
# guard as every other *_send.sh sibling, op#21145).
source "$ORCH_DIR/scripts/lib/client_send_leak_guard.sh"
_client_send_leak_guard "$TEXT" || exit 3

# Scrub secret PATTERNS (pg DSNs, bot tokens, API keys) BEFORE anything leaves
# the process — same baseline pass as angullia_send.sh/oeh_send.sh. Clean input
# round-trips byte-identical; a redactor hiccup must not drop the message, so
# fall back to the original text on failure.
if REDACTED="$(printf '%s' "$TEXT" | PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.secret_redact 2>/dev/null)"; then
  TEXT="$REDACTED"
fi

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
[ "$sent" = 1 ] && exit 0 || { echo "mamadah_send failed" >&2; exit 1; }
