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
# DISCRIMINATOR — why NOT ORCH_BODY_ROLE (2026-09-11 regression, coord-caught): the shared
# ~/wingmen/orchestrator/.env carries ORCH_BODY_ROLE=console (+ ORCH_AGENT_ID, ORCH_TMUX_SESSION).
# launch_dangerous_cc.sh UNSETS those at lane boot, BUT a lane's command that re-`source`s .env for
# its DSN/tokens RE-INTRODUCES ORCH_BODY_ROLE=console — so the first cut here wrongly blocked coord's
# routine reviewer_send. env vars that live in the shared .env are unusable as a body discriminator.
#
# The reliable signal: launch_dangerous_cc.sh EXPORTS CC_BASE_AGENT_ID (the lane's family id, e.g.
# cc-irsyad-coord) into every lane process. It is NOT in the shared .env, so a lane sourcing .env
# cannot clear it, and it is inherited by every subprocess incl reviewer_send. The console (Nazim,
# booted via boot_nazim.sh) has NO CC_BASE_AGENT_ID. VERIFIED 2026-09-11: console env has none; all
# 10 live lanes carry CC_BASE_AGENT_ID=cc-*. So: a caller WITH CC_BASE_AGENT_ID is a lane → exempt;
# WITHOUT it → the console body → gate. (The hub, also without it, is on another host + shouldn't
# routine-send irsyad client either; the override covers a deliberate hub/console send.)
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
  # Only the irsyad client channel(s) are gated.
  local gated=0 ch
  for ch in $CONSOLE_IRSYAD_CLIENT_CHANNELS; do
    [ "$target" = "$ch" ] && { gated=1; break; }
  done
  [ "$gated" = 1 ] || return 0
  # LANE EXEMPTION (robust, .env-pollution-proof): any lane carries an exported CC_BASE_AGENT_ID
  # that the console never has. coord and every other lane send freely; only the console is gated.
  [ -n "${CC_BASE_AGENT_ID:-}" ] && return 0
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
