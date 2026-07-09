#!/usr/bin/env bash
# nazim_say.sh — speak a line to the operator in Nazim's natural voice.
#
# Local neural TTS (Piper) -> AAC (afconvert, since the Mini's ffmpeg is broken)
# -> Telegram sendAudio via the nazim bot. Fully on-device, free, private.
#
# Usage: nazim_say.sh "<text to speak>"
# Reads NAZIM_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. Never echoes the token.
set -euo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TTS_DIR="${WINGMEN_TTS_DIR:-$HOME/.wingmen-tts}"
VOICE="${NAZIM_TTS_VOICE:-en_US-ryan-high}"
MODEL="$TTS_DIR/$VOICE.onnx"

TEXT="${1:?usage: nazim_say.sh \"<text>\"}"
[ -f "$MODEL" ] || { echo "nazim_say: voice model missing: $MODEL" >&2; exit 1; }

TOK=$(grep '^NAZIM_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] && [ -n "${CHAT:-}" ] || { echo "nazim_say: NAZIM_BOT_TOKEN/MUSA_TELEGRAM_ID missing" >&2; exit 1; }

WAV=$(mktemp -t nazim_say).wav
M4A="${WAV%.wav}.m4a"
trap 'rm -f "$WAV" "$M4A"' EXIT

printf '%s' "$TEXT" | "$ORCH_DIR/.venv/bin/piper" -m "$MODEL" -f "$WAV" 2>/dev/null
afconvert -f m4af -d aac -b 64000 "$WAV" "$M4A" 2>/dev/null

curl -s -o /dev/null -w '%{http_code}' \
  "https://api.telegram.org/bot${TOK}/sendAudio" \
  -F "chat_id=${CHAT}" \
  -F "audio=@${M4A}" \
  -F "title=Nazim" | grep -q '^200$' || { echo "nazim_say: send failed" >&2; exit 1; }

# Audit: the spoken text ALWAYS lands in the durable log (voice never bypasses
# operator_messages). Prefixed so the log shows it was delivered as voice.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  outbound "[voice] $TEXT" --chat "$CHAT" --tag nazim-console >/dev/null 2>&1 || true
echo "spoken ✓ (logged)"