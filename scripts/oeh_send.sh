#!/usr/bin/env bash
# oeh_send.sh — send a message to the OEH support channel,
# sibling to scripts/angullia_send.sh (op#22521, bus #43713).
#
# Supervised shape: a reviewer (cc-oeh, per bot_channels.group_routing
# on channel_key='oeh') sends here — the raw builder lane never sends
# directly (though for oeh coord and builder are the same lane). Reads
# OEH_BOT_TOKEN from .env. NEVER echoes the token.
#
# NOT gated by console_irsyad_client_send_gate.sh: that gate is op#21944's
# irsyad-specific console/execution key-separation control and does not
# extend to oeh. Generic anti-leak / arg-swap guards still apply.
#
# Usage:
#   scripts/oeh_send.sh "message text"
#   echo "message" | scripts/oeh_send.sh
#   TG_CHAT_OVERRIDE=<chat_id> scripts/oeh_send.sh "message text"
#   scripts/oeh_send.sh --secret-vault-key oeh_preview_password "password: {{SECRET}}"
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^OEH_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^OEH_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "OEH_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "OEH_CHAT_ID missing from .env (and no TG_CHAT_OVERRIDE)" >&2; exit 1; }

# --secret-vault-key <name> (bus #44378, after op#22696 + the 2026-09-27 OEH
# preview-password leak into operator_messages row 22824): the RESOLVED value
# must never pass through argv or the durable log. Give the message a literal
# {{SECRET}} placeholder instead of the real value; this script fetches the
# vault value itself and substitutes it ONLY into the copy that gets sent —
# the copy that gets logged keeps the placeholder. Scanned out of argv before
# the positional TEXT/TAG, same shape as tg_send.sh/nazim_send.sh's --ask.
SECRET_VAULT_KEY=""
_POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --secret-vault-key) SECRET_VAULT_KEY="${2:-}"; shift 2 ;;
    *) _POSITIONAL+=("$1"); shift ;;
  esac
done
if [ "${#_POSITIONAL[@]}" -gt 0 ]; then
  set -- "${_POSITIONAL[@]}"
else
  set --
fi

TEXT="${1:-$(cat)}"
TAG="${2:-oeh}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }
# Fail loud on the channel-first arg-swap footgun (op#16353, same guard as irsyad's).
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$TEXT" || exit 2
_send_tag_shape_guard "$TAG" || exit 2

# op#21145 (Musa direct): no internal identity / escalation framing reaches a client group.
source "$ORCH_DIR/scripts/lib/client_send_leak_guard.sh"
_client_send_leak_guard "$TEXT" || exit 3

# Scrub secret PATTERNS (pg DSNs, bot tokens, API keys) BEFORE anything leaves
# the process — the same pass tg_send.sh/nazim_send.sh already apply. This
# script had none of this until bus #44378: the vault preview-password leak
# itself had no fixed shape to match here (that's what --secret-vault-key
# above is for), but oeh_send.sh should not be the one sibling with zero
# baseline pattern hygiene. Clean input round-trips byte-identical; a
# redactor hiccup must not drop the message, so fall back to the original
# text on failure.
if REDACTED="$(printf '%s' "$TEXT" | PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.secret_redact 2>/dev/null)"; then
  TEXT="$REDACTED"
fi

# Resolve {{SECRET}} for the SEND ONLY. TEXT (used for the durable log below)
# keeps the placeholder — the resolved value lives only in SEND_TEXT, passed
# to the sender via env (never argv, never the log).
SEND_TEXT="$TEXT"
if [ -n "$SECRET_VAULT_KEY" ]; then
  if ! SEND_TEXT="$(VPH_TEMPLATE="$TEXT" VPH_VAULT_KEY="$SECRET_VAULT_KEY" PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/vault_placeholder_send.py")"; then
    echo "oeh_send: --secret-vault-key substitution failed (see above)" >&2
    exit 4
  fi
fi

# Send (chunked at Telegram's 4096-char limit so long replies aren't truncated).
# token/chat/text passed via env, never argv — keeps the token out of `ps`.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$SEND_TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
# durable log every reply (full text, once; best-effort — never fail on a log hiccup).
# CAI-598: log DELIVERY, not intent — --undelivered on a failed send.
# TEXT (not SEND_TEXT): the resolved secret value must never reach this log.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" --tag "$TAG" $([ "$sent" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "oeh_send failed" >&2; exit 1; }
