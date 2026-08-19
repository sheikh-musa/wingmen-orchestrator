#!/usr/bin/env bash
# fleet_model.sh — flip the fleet's Claude model in one place (token conservation).
#
# WHY: On Max, Opus and Sonnet draw down SEPARATE weekly limits. When the Opus
# window is low, push bulk/mechanical engineer lanes to Sonnet to preserve Opus
# for work that needs it. This is the one-switch lever.
#
# HOW: writes $ORCH_DIR/.fleet_model, which launch_dangerous_cc.sh reads as the
# default model for NEW lane launches (precedence: MODEL env > .fleet_model > opus).
# With --live it ALSO flips already-running lanes via lane_nudge.sh (VERIFIED
# submit — never a bare send-keys Enter). Core brains (orch, cai) are left on
# their current model unless --all is passed.
#
# Usage:
#   fleet_model.sh                      show current setting + effective default
#   fleet_model.sh <opus|sonnet|haiku|fable|claude-*>   set default for NEW lanes
#   fleet_model.sh <model> --live       also flip running engineer lanes now
#   fleet_model.sh <model> --live --all  include orch + cai in the live flip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$ORCH_DIR/.fleet_model"
NUDGE="$SCRIPT_DIR/lane_nudge.sh"
# Sessions the live-flip must NEVER send /model into: orch (hub) + cai are the
# strategic core brains (pass --all to include them); nazim (this console body),
# fleet-health (the SRE), and fleet-console (a Python server, not a claude REPL —
# a /model nudge would corrupt it) are Mini infrastructure bodies, never engineer
# lanes. Excluding them keeps --live scoped to the actual engineer lanes.
CORE_LANES="orch cai nazim fleet-health fleet-console"
# FULL-tier auditors (cai CAI-RESP-1170): cc-quality + cc-storefront render governance
# verdicts on money-path / live-tenant work, which requires being PINNED to claude-opus-4-8
# EXACTLY. A blanket `fleet_model.sh sonnet --live` conservation flip once swept them to
# sonnet silently and downgraded a live money-path audit (2026-08-19 incident). Carve-out: a
# --live flip whose target is NOT exactly claude-opus-4-8 SKIPS these lanes — refusing BOTH a
# downgrade (sonnet/haiku) AND an over-correction drift (opus-5, which op#9020 + cai bar for
# these lanes: '4.8 not 5'). --all still forces (a deliberate model change updates the pin).
# Same shape as the CORE_LANES governance carve-out.
AUDITOR_LANES="quality storefront"
DEFAULT_MODEL="claude-opus-4-8"

resolve() {  # alias|full-id -> full model id
  case "$1" in
    opus)      echo "claude-opus-4-8" ;;
    sonnet)    echo "claude-sonnet-5" ;;
    haiku)     echo "claude-haiku-4-5-20251001" ;;
    fable)     echo "claude-fable-5" ;;
    claude-*)  echo "$1" ;;
    *) echo "ERR" ;;
  esac
}

# --- no-arg: status ---
if [ $# -eq 0 ] || [ "${1:-}" = "status" ]; then
  if [ -r "$CONFIG" ] && [ -n "$(tr -d '[:space:]' < "$CONFIG")" ]; then
    echo "FLEET_MODEL (new lanes): $(tr -d '[:space:]' < "$CONFIG")   [$CONFIG]"
  else
    echo "FLEET_MODEL (new lanes): (unset) -> falls back to $DEFAULT_MODEL"
  fi
  echo "Live lanes:"; tmux ls 2>/dev/null | sed 's/^/  /' || echo "  (no tmux sessions)"
  exit 0
fi

FULL="$(resolve "$1")"
if [ "$FULL" = "ERR" ]; then
  echo "ERROR: unknown model '$1' (use opus|sonnet|haiku|fable or a claude-* id)" >&2; exit 2
fi

# --- write the config (affects NEW lane launches) ---
printf '%s\n' "$FULL" > "$CONFIG"
echo "✔ FLEET_MODEL set to $FULL  ($CONFIG)"
echo "  New engineer-lane launches will use it (MODEL env still overrides per-lane)."

# --- optional: flip running lanes now ---
LIVE=0; ALL=0
shift
for a in "$@"; do
  case "$a" in
    --live) LIVE=1 ;;
    --all)  ALL=1 ;;
    *) echo "WARN: ignoring unknown flag '$a'" >&2 ;;
  esac
done

if [ "$LIVE" -eq 1 ]; then
  [ -x "$NUDGE" ] || { echo "ERROR: $NUDGE not found/executable — cannot flip live lanes safely" >&2; exit 3; }
  echo "Flipping running lanes to $FULL (verified submit)…"
  flipped=0; skipped=0
  while IFS= read -r sess; do
    [ -z "$sess" ] && continue
    if [ "$ALL" -eq 0 ] && printf '%s ' $CORE_LANES | grep -qw "$sess"; then
      echo "  · $sess — SKIP (core brain; pass --all to include)"; skipped=$((skipped+1)); continue
    fi
    # FULL-auditor carve-out (CAI-RESP-1170): PIN to claude-opus-4-8 exactly. Skip any target
    # that isn't exactly opus-4-8 — refusing a downgrade (sonnet/haiku) AND an opus-5 drift.
    if [ "$ALL" -eq 0 ] && printf '%s ' $AUDITOR_LANES | grep -qw "$sess" && [ "$FULL" != "claude-opus-4-8" ]; then
      echo "  · $sess — SKIP (FULL auditor: pinned to opus-4-8, refusing $FULL; pass --all to force)"; skipped=$((skipped+1)); continue
    fi
    if "$NUDGE" "$sess" "/model $FULL"; then
      echo "  ✔ $sess — flipped"; flipped=$((flipped+1))
    else
      echo "  ✗ $sess — submit NOT verified (flip manually with /model $FULL)" >&2
    fi
  done < <(tmux ls -F '#{session_name}' 2>/dev/null || true)
  echo "Live flip: $flipped flipped, $skipped skipped."
else
  echo "  To apply to RUNNING lanes: re-run with --live, or restart lanes (scripts/lanes.sh up),"
  echo "  or type '/model $FULL' in each live session."
fi
