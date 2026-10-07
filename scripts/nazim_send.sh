#!/usr/bin/env bash
# nazim_send.sh — Nazim (console/CTO body) → operator via @nazim_cto_bot.
#
# This is the console body's OWN Telegram voice: a DISTINCT bot identity
# (NAZIM_BOT_TOKEN) on a DISTINCT channel (nazim-console) — NEVER @wingmennorchbot.
# It therefore does NOT pass through the ORCH-TOPOLOGY-001 pen-(iv) gate: that
# gate protects the HUB's operator-orch channel, which this script structurally
# cannot touch (token + channel are hard-wired to Nazim's own). Identity
# separation by token+channel is exactly the CAI-RESP-389 design. The gate on
# tg_send.sh still fail-closes the console body out of the hub's channel — this
# is the sanctioned counterpart that lets Nazim speak as himself.
#
# Reads NAZIM_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:  scripts/nazim_send.sh "message text"
#         echo "message" | scripts/nazim_send.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^NAZIM_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
# Precedence: TG_CHAT_OVERRIDE, then an EXPLICIT environment MUSA_TELEGRAM_ID,
# then .env. The env leg is not cosmetic — tests/conftest.py sets
# MUSA_TELEGRAM_ID=123456 expressly to keep test runs off the operator's phone,
# and this line used to ignore it by re-reading .env off disk, so that safeguard
# was decorative. On 2026-07-26 a pytest run duly paged the operator twice with a
# false "hub cleared" claim. An env var that looks like it disarms something must
# actually disarm it.
CHAT="${TG_CHAT_OVERRIDE:-${MUSA_TELEGRAM_ID:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}}"
[ -n "${TOK:-}" ] || { echo "NAZIM_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }

# --ask/--chase-hours (op#22669): mark THIS outbound as a genuine ask of the
# operator (opens its own operator_asks row via scripts/asks_open.py, linked to
# this send so a reply auto-closes it). Scanned out of argv BEFORE the
# positional TEXT — see scripts/tg_send.sh's identical parsing for the hub side.
ASK=""
CHASE_HOURS=""
# --secret-vault-key <name> (bus #44378): see tg_send.sh's identical block for
# the full rationale. The resolved value never touches argv or the log.
SECRET_VAULT_KEY=""
_POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ask) ASK="${2:-}"; shift 2 ;;
    --chase-hours) CHASE_HOURS="${2:-}"; shift 2 ;;
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
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }
# Fail loud on the channel-first arg-swap footgun (op#16353).
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$TEXT" || exit 2

# Scrub secret patterns (pg DSNs, bot tokens, API keys) BEFORE anything leaves
# the process — the send AND the durable log both use the scrubbed text. Clean
# input round-trips byte-identical; a redactor hiccup must not drop the message,
# so fall back to the original text on failure.
if REDACTED="$(printf '%s' "$TEXT" | PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.secret_redact 2>/dev/null)"; then
  TEXT="$REDACTED"
fi

# 2026-10-07 (orch-console #57970): refuse a weekday paired with the wrong date. Fail-closed.
source "$ORCH_DIR/scripts/lib/date_weekday_guard.sh"
_date_weekday_guard "$TEXT" || exit 6

# Resolve {{SECRET}} for the SEND ONLY (bus #44378). TEXT (used for the
# durable log below) keeps the placeholder — the resolved value lives only in
# SEND_TEXT, passed to the sender via env (never argv, never the log).
SEND_TEXT="$TEXT"
if [ -n "$SECRET_VAULT_KEY" ]; then
  if ! SEND_TEXT="$(VPH_TEMPLATE="$TEXT" VPH_VAULT_KEY="$SECRET_VAULT_KEY" PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/vault_placeholder_send.py")"; then
    echo "nazim_send: --secret-vault-key substitution failed (see above)" >&2
    exit 5
  fi
fi

# Send (chunked at Telegram's 4096-char limit). token/chat/text via env, not argv.
# TG_FAIL_OUT: the helper writes the structured failure (status/description/retry_after)
# here so we can record WHY on the operator_messages row (Nazim #40837).
# TG_MSGID_OUT (op#22669): helper writes Telegram's own message_id for the sent
# message, so a later reply_to_message can be matched back to this exact row.
FAILOUT="$(mktemp)"
MSGIDOUT="$(mktemp)"
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$SEND_TEXT" TG_FAIL_OUT="$FAILOUT" TG_MSGID_OUT="$MSGIDOUT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
TGMSGID="$(cat "$MSGIDOUT" 2>/dev/null || true)"

# Durable log every reply (tag=nazim-console → scopes to the console body and
# keeps the two-way thread coherent for a rebooted Nazim). Best-effort.
# `delivered` MUST reflect what actually happened. operator_log defaults delivered=TRUE, so
# calling it unconditionally records a FAILED send as delivered — which is what it did here until
# now, on every message this script has ever sent. Found fleet-wide by cc-orchestrator (25701aa)
# after a real client asked the same question three times while our log showed every reply
# delivered; this script was NOT in that fix. `delivered` is evidence or it is decoration.
# OPLOGID (op#22669): captures the logged row's id so a --ask open can link back to it.
if [ "$sent" = 1 ]; then
  OPLOGID="$(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
    outbound "$TEXT" --chat "$CHAT" --tag nazim-console \
    ${TGMSGID:+--tg-message-id "$TGMSGID"} 2>/dev/null)" || true
else
  REASON="$(cat "$FAILOUT" 2>/dev/null || true)"
  OPLOGID=""
  PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
    outbound "$TEXT" --chat "$CHAT" --tag nazim-console --undelivered \
    ${REASON:+--reason "$REASON"} >/dev/null 2>&1 || true
fi
rm -f "$FAILOUT" "$MSGIDOUT"

# --ask (op#22669): this send is ITSELF a genuine ask of the operator — open its
# own operator_asks row (waiting_on_operator=true), linked to the row just
# logged so a genuine reply auto-closes it. Best-effort: an asks_open.py hiccup
# must never fail the send itself (the message already reached the operator).
if [ -n "$ASK" ] && [ "$sent" = 1 ]; then
  "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/asks_open.py" "$ASK" \
    --delegated-to orch-console \
    ${CHASE_HOURS:+--chase-hours "$CHASE_HOURS"} \
    ${OPLOGID:+--outbound-msg-id "$OPLOGID"} \
    >/dev/null 2>&1 || true
fi

[ "$sent" = 1 ] && exit 0 || { echo "nazim_send failed" >&2; exit 1; }
