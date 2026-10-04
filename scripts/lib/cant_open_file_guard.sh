#!/usr/bin/env bash
# cant_open_file_guard.sh — fail-closed guard against telling a CLIENT "I can't
# open your file" (or asking them to describe it) instead of staging it.
#
# WHY (orch-console bus #51060, Musa op#25437-25440, angry): cc-cosem-exams told
# Hariz it couldn't open his spreadsheet, then asked him to describe it — a
# lane must never read a client's raw file itself, and must never put that on
# the client either. The correct path: ack the client, and route the file to
# orch-console for staging (scripts/stage_client_file.py) — never say "I can't
# open/read/access/view it" or "describe what it shows". Enforced in code at
# both client-send chokepoints (lane_reply.sh, reviewer_send.sh), not left to
# memory (same discipline as client_send_leak_guard.sh).
#
# Sourced by lane_reply.sh / reviewer_send.sh; called with the resolved
# outbound text:
#   source "$ORCH_DIR/scripts/lib/cant_open_file_guard.sh"
#   _cant_open_file_guard "$TEXT" || exit 5
#
# No escape hatch by design (unlike client_send_leak_guard.sh's deliberate-
# override precedent) — the operator's complaint is specifically about using
# this framing AT ALL; a client-facing script needing to report a genuinely
# corrupted file should ack + stage instead, never describe-it-yourself.
_cant_open_file_guard() {
  local text="$1"
  local lc; lc="$(printf '%s' "$text" | tr '[:upper:]' '[:lower:]')"
  if printf '%s' "$lc" | grep -qE \
      "can.?t open|cannot open|unable to open|can.?t (read|access|view) (the |your )?(file|attachment|spreadsheet|document)|describe (it|what it shows)"; then
    echo "ERROR: client-bound message tells the client the file can't be opened / asks them to describe it (orch-console #51060)." >&2
    echo "Never open a client's raw file yourself and never put that framing on them — ack the client instead and route the file to orch-console for staging (scripts/stage_client_file.py)." >&2
    return 5
  fi
  return 0
}
