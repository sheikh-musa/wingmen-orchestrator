#!/usr/bin/env bash
# tg_send.sh — send a message to the operator via the Wingmen Orchestrator bot
# (@wingmennorchbot). The outbound half of the 2-way bridge: cc-orchestrator
# (and the daily brief) call this to reach the operator's phone.
#
# Reads WINGMEN_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:
#   scripts/tg_send.sh "message text"
#   echo "message" | scripts/tg_send.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^WINGMEN_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
# Default target = the operator (Musa). TG_CHAT_OVERRIDE lets the bridge route a
# reply to a different authorized chat (e.g. the shipforge/storefront group).
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "WINGMEN_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }

# ORCH-TOPOLOGY-001 pen (iv): only the lease-holder hub speaks on this channel.
# Fail-closed for the console body (Nazim); fail-safe for the hub (never strand it).
if ! GATE=$("$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/orch_lease.py" check); then
  echo "tg_send REFUSED — $GATE" >&2
  mkdir -p "$ORCH_DIR/logs" 2>/dev/null || true
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') tg_send REFUSED (pen iv) :: ${1:-<stdin>}" >> "$ORCH_DIR/logs/pen_gate.log" 2>/dev/null || true
  exit 3
fi

# --ask/--chase-hours (op#22669): mark THIS outbound as a genuine ask of the
# operator (opens its own operator_asks row via scripts/asks_open.py, linked
# to this send so a reply auto-closes it). Scanned out of argv BEFORE the
# positional TEXT/TAG so callers can still pass either shape. Not positional
# themselves — no existing caller ever passes a literal "--ask" as message text.
ASK=""
CHASE_HOURS=""
# --secret-vault-key <name> (bus #44378, after op#22696 + the 2026-09-27 OEH
# preview-password leak into operator_messages row 22824): the RESOLVED
# secret value must never pass through argv or the durable log. Give the
# message a literal {{SECRET}} placeholder instead of the real value; this
# script fetches the vault value itself and substitutes it ONLY into the copy
# that gets sent — the copy that gets logged keeps the placeholder.
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
TAG="${2:-}"   # optional @alias context this reply pertains to
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }
# Fail loud on the channel-first arg-swap footgun (op#16353).
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$TEXT" || exit 2
_send_tag_shape_guard "$TAG" || exit 2

# Scrub secret patterns (pg DSNs, bot tokens, API keys) BEFORE anything leaves
# the process — the send AND the durable log both use the scrubbed text. Clean
# input round-trips byte-identical; a redactor hiccup must not drop the message,
# so fall back to the original text on failure.
if REDACTED="$(printf '%s' "$TEXT" | PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.secret_redact 2>/dev/null)"; then
  TEXT="$REDACTED"
fi

# Resolve {{SECRET}} for the SEND ONLY (bus #44378). TEXT (used for the
# durable log below) keeps the placeholder — the resolved value lives only in
# SEND_TEXT, passed to the sender via env (never argv, never the log).
SEND_TEXT="$TEXT"
if [ -n "$SECRET_VAULT_KEY" ]; then
  if ! SEND_TEXT="$(VPH_TEMPLATE="$TEXT" VPH_VAULT_KEY="$SECRET_VAULT_KEY" PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/vault_placeholder_send.py")"; then
    echo "tg_send: --secret-vault-key substitution failed (see above)" >&2
    exit 5
  fi
fi

# Send (chunked at Telegram's 4096-char limit so long replies aren't truncated).
# token/chat/text passed via env, never argv — keeps the token out of `ps`.
# TG_FAIL_OUT: helper writes the structured failure so we record WHY on the row (#40837).
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
# durable log every reply (full text, once; best-effort — never fail on a log hiccup).
# PYTHONPATH pins the package root so the `-m` import works regardless of CWD (a
# bare `-m nervous_system.operator_log` only resolves when run from $ORCH_DIR;
# any other caller-CWD would ModuleNotFoundError and silently drop the log).
# CAI-598: log DELIVERY, not intent. This log line used to run unconditionally with delivered
# defaulting to TRUE, so a failed send was recorded as delivered. Ported from the Studio's fix
# (25701aa) — which had been applied there and NOT here: the fix was host-split, each machine
# carrying half of it, for the same two-host reason that has bitten five times today.
REASON=""; [ "$sent" = 1 ] || REASON="$(cat "$FAILOUT" 2>/dev/null || true)"
# Capture the logged row's id (op#22669): needed to link a --ask open to THIS
# outbound send. Previously discarded to /dev/null — now captured, stderr still
# discarded so a redirect hiccup can't corrupt the id we read.
OPLOGID="$(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" ${TAG:+--tag "$TAG"} $([ "$sent" = 1 ] || echo --undelivered) ${REASON:+--reason "$REASON"} ${TGMSGID:+--tg-message-id "$TGMSGID"} 2>/dev/null)" || true
rm -f "$FAILOUT" "$MSGIDOUT"

# --ask (op#22669): this send is ITSELF a genuine ask of the operator — open its
# own operator_asks row (waiting_on_operator=true), linked to the row just
# logged so a genuine reply auto-closes it. Best-effort: an asks_open.py hiccup
# must never fail the send itself (the message already reached the operator).
if [ -n "$ASK" ] && [ "$sent" = 1 ]; then
  "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/asks_open.py" "$ASK" \
    ${CHASE_HOURS:+--chase-hours "$CHASE_HOURS"} \
    ${OPLOGID:+--outbound-msg-id "$OPLOGID"} \
    >/dev/null 2>&1 || true
fi
[ "$sent" = 1 ] && exit 0 || { echo "tg_send failed" >&2; exit 1; }
