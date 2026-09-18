#!/usr/bin/env bash
# client_send_leak_guard.sh — fail-closed guard against leaking INTERNAL identities or
# INTERNAL escalation framing into a message bound for an external CLIENT / SME group.
#
# WHY (op#21145, Musa direct, 2026-09-18): a reply to the irsyad operator (Shuq) said
# "I'll give Musa a heads-up since it changes a money control." That was wrong twice over:
# it exposed an internal person (Musa) to the client, and it framed the CLIENT OPERATOR's
# own authoritative decision as if it needed sign-off above them. The client operators
# (Shuq/Wan for irsyad, Hariz for cosem) GOVERN their own project per the governance
# console (operators + cai + money-clearance are set there); their operational decisions
# are theirs. Client-facing messages therefore carry ZERO internal personnel names and ZERO
# "we must check above you" escalation. Musa: "ensure it never happens again" — so this is
# enforced in code at the one client-send chokepoint (lane_reply.sh), not left to memory.
#
# Sourced by lane_reply.sh; called with the resolved outbound text:
#   source "$ORCH_DIR/scripts/lib/client_send_leak_guard.sh"
#   _client_send_leak_guard "$TEXT" || exit 3
#
# Fail-closed: a match BLOCKS the send with a rewrite instruction. A rare false positive
# costs a reword — the right tradeoff for "never again". Keep the list tight + word-bounded.
_client_send_leak_guard() {
  local text="$1"
  local lc; lc="$(printf '%s' "$text" | tr '[:upper:]' '[:lower:]')"
  # Internal identities (fleet personnel/agents) — never belong in a client message.
  # Internal governance jargon / escalation framing that implies the operator's own call
  # needs sign-off above them.
  local patterns=(
    '\bmusa\b'
    '\bnazim\b'
    'cc-cai'
    '\bcc-orchestrator\b'
    '\borch-console\b'
    'money-clearance'
    "heads-up musa"
    "give musa a heads"
  )
  local pat
  for pat in "${patterns[@]}"; do
    if printf '%s' "$lc" | grep -qE "$pat"; then
      echo "ERROR: client-bound message contains an internal identity / escalation phrase (matched: '${pat}')." >&2
      echo "Client operators govern their own project — never expose internal personnel (Musa/Nazim/cai/…) and never imply their decision needs sign-off above them (op#21145)." >&2
      echo "Rewrite as if speaking to the decision-maker (because they are), then resend." >&2
      return 3
    fi
  done
  return 0
}
