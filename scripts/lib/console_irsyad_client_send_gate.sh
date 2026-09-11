#!/usr/bin/env bash
# console_irsyad_client_send_gate.sh — fail-closed gate: the CONSOLE body (Nazim) may
# NOT fire ROUTINE irsyad CLIENT replies. Only a deliberate money/floor statement reaches
# the client through console, via an explicit reasoned (logged) override.
#
# WHY (Musa, 2026-09-11, op 19756/19762/19767 + frustration 19781): console was OVER-DRIVING
# irsyad — the client-reply release bottleneck AND re-running verification coord already did,
# making coord look redundant and frustrating Gazzabyte with promise-without-follow-through.
# The DIVISION Musa mandated: irsyad-coord owns the irsyad workstream + CLIENT-COMMS DIRECTLY
# (coord's own reviewer_send is ungated); console gates ONLY money / factual-client-statements /
# floor-PII / merge-deploy-migration sign-off. A promise from console does not survive a context
# reset — so this is enforce-in-code, not another "I'll do better." A control that needs
# remembering is a sentence.
#
# Fires ONLY for the console body: ORCH_BODY_ROLE=console (the SAME discriminator orch_lease.py
# trusts, _role()). coord/lanes/hub carry no ORCH_BODY_ROLE=console — VERIFIED 2026-09-11 that
# coord's live process env has it absent (79 vars captured, 0 ORCH_BODY_ROLE lines) — so their
# sends pass freely. This restricts CONSOLE ONLY; it can never block coord's client comms.
#
# Override (a deliberate money/floor send console is entitled to make):
#   export IRSYAD_CLIENT_SEND_OK="<why this is money/floor>"   # non-empty reason
# The reason is appended to logs/console_irsyad_send_overrides.log for attribution.
#
# Usage (source, then call with the RESOLVED target channel/tag, before sending):
#   source "$ORCH_DIR/scripts/lib/console_irsyad_client_send_gate.sh"
#   _console_irsyad_client_send_gate "$CHANNEL_OR_TAG" || exit 4

# The irsyad CLIENT channels/tags console must not routine-send into (space-separated,
# overridable so a new irsyad client surface is one edit away — assert the TOTAL, not a
# single known path).
: "${CONSOLE_IRSYAD_CLIENT_CHANNELS:=gazzabyte-irsyad}"

_console_irsyad_client_send_gate() {
  local target="${1:-}"
  # Only the console body is gated.
  [ "${ORCH_BODY_ROLE:-}" = "console" ] || return 0
  # Only the irsyad client channel(s) are gated.
  local gated=0 ch
  for ch in $CONSOLE_IRSYAD_CLIENT_CHANNELS; do
    [ "$target" = "$ch" ] && { gated=1; break; }
  done
  [ "$gated" = 1 ] || return 0
  # Deliberate money/floor override: an explicit, non-empty, logged reason.
  if [ -n "${IRSYAD_CLIENT_SEND_OK:-}" ]; then
    local logf="${ORCH_DIR:-$HOME/wingmen/orchestrator}/logs/console_irsyad_send_overrides.log"
    printf '%s console money/floor send to %s: %s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$target" "$IRSYAD_CLIENT_SEND_OK" >> "$logf" 2>/dev/null || true
    echo "⚠️  console irsyad client-send OVERRIDE (money/floor): $IRSYAD_CLIENT_SEND_OK" >&2
    return 0
  fi
  cat >&2 <<EOF
REFUSED: the console body (Nazim) may not send a ROUTINE irsyad client reply to '$target'.
Division (Musa, op 19756+): irsyad-coord owns client-comms DIRECTLY — route this through coord
  (coord runs:  scripts/reviewer_send.sh $target "<reply>").
Console gates money/floor ONLY. If this IS a deliberate money-fact / floor statement you are
gating, re-send with an explicit reason:
  IRSYAD_CLIENT_SEND_OK="<why this is money/floor>" <your send command>
EOF
  return 4
}
