#!/usr/bin/env bash
# reset_lane.sh — in-place /clear + boot of a Mini CC LANE session (cc-irsyad,
# cc-cosem-platform, etc.). Generic sibling of reset_orch.sh / reset_cai.sh for
# lanes that have no body-specific reset script.
#
# Usage:  scripts/reset_lane.sh <tmux-session> "<boot instruction>"
#   e.g.  scripts/reset_lane.sh irsyad "You are cc-irsyad, freshly reset. Read
#         reports/cc-irsyad-charter-20260725.md IN FULL, reconcile your bus ..."
#
# Same safety as reset_orch.sh: refuses a BUSY lane (RESET_FORCE=1 overrides,
# loudly); captures + preserves any staged composer text before wiping; verifies
# the composer is empty before sending /clear; boots with the caller's instruction.
# In-place /clear keeps the session/worktree/identity — only the conversation is
# cleared, and the boot instruction reconstitutes the task state.
#
# Mini lanes run on the /usr/local/bin/tmux server (socket tmux-501/default), NOT
# /opt/homebrew/bin/tmux — see reference_mini_tmux_two_binaries_socket.
set -uo pipefail
cd "$HOME/wingmen/orchestrator" || { echo "ERROR: orch dir missing" >&2; exit 9; }
set -a; source .env; set +a

_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
. "$_LIB/composer_capture.sh" || { echo "ERROR: composer_capture.sh missing" >&2; exit 9; }

TM="${TM:-/usr/local/bin/tmux}"
[ -x "$TM" ] || TM="$(command -v tmux || echo /usr/local/bin/tmux)"
SESS="${1:?usage: reset_lane.sh <tmux-session> \"<boot instruction>\"}"
BOOT_IN="${2:?missing boot instruction}"
PANE="${SESS}:0.0"

if ! "$TM" has-session -t "$SESS" 2>/dev/null; then
  echo "ERROR: tmux session '$SESS' not found on this host ($TM)." >&2; exit 1
fi

# SELF-FIRE GUARD (CAI-779 Tier-B, mirrors reset_nazim.sh d975d1a — cc-quality-verified).
# A body may PREP its own recycle but must NEVER fire its own /clear: from INSIDE the
# target session the send-keys below interleave with the caller's live turn and stage a
# boot-before-clear half-state (op#11269/11271). A process launched in a tmux pane
# inherits $TMUX_PANE; resolve its session and refuse if it IS the target. Here $SESS is
# the PARAMETERIZED lane arg, so the compare is against the resolved lane session (not a
# constant). External callers (operator reset button, ssh, another session's pane,
# non-tmux shell) all pass — fail-open (`|| echo` -> empty != $SESS) so a resolver hiccup
# never blocks a real reset.
if [ -n "${TMUX_PANE:-}" ]; then
  _caller_sess="$("$TM" display-message -p -t "${TMUX_PANE}" '#S' 2>/dev/null || echo)"
  if [ "$_caller_sess" = "$SESS" ]; then
    echo "[reset_lane] SELF-FIRE REFUSED: invoked from INSIDE the '$SESS' lane session — a lane cannot /clear its own live turn (boot-before-clear). Fire it EXTERNALLY (operator reset button, ssh, or another session's pane)." >&2
    if [ "${RESET_ALLOW_SELF:-0}" != 1 ]; then exit 5; fi
    echo "[reset_lane] RESET_ALLOW_SELF=1 — proceeding despite self-fire (NOT for a real recycle)." >&2
  fi
fi

pane_busy "$TM" "$PANE"
if [ "${CC_BUSY_STALE:-0}" = 1 ]; then
  echo "WARNING: '$SESS' shows a background-agent marker but the pane is FROZEN (byte-identical)." >&2
  echo "         Treating as NOT busy: a live wait animates. In-flight work, if any, is already lost." >&2
fi
if [ "$CC_BUSY" = 1 ]; then
  if [ "${RESET_FORCE:-0}" = "1" ]; then
    echo "WARNING: '$SESS' is BUSY — $CC_BUSY_REASON — RESET_FORCE=1, clearing ANYWAY (work DISCARDED)." >&2
  else
    echo "ERROR: '$SESS' is BUSY — $CC_BUSY_REASON — refusing to clear. Set RESET_FORCE=1 to override." >&2
    exit 5
  fi
fi

# Capture + preserve any staged composer text before the wipe destroys it.
composer_parse_pane "$TM" "$PANE"
STAGED_NOTE="NOTE: your composer was EMPTY when I cleared you — nothing was staged, nothing lost."
if [ "$CC_EMPTY" != 1 ] && [ "${CC_PARTIAL:-}" != 'noprompt' ]; then
  mkdir -p logs
  printf '%s reset_lane[%s] staged: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SESS" "$CC_FLAT" >> logs/reset_lane_preserved_input.log
  echo "[reset_lane] PRESERVED staged composer: $CC_FLAT"
  STAGED_NOTE="NOTE: you had \"${CC_FLAT}\" staged UNSENT when I cleared you (captured verbatim to logs/reset_lane_preserved_input.log). Judge whether it is your own next step or an instruction typed at your pane and not carried out yet; it is yours to re-decide."
elif [ "${CC_PARTIAL:-}" = 'noprompt' ]; then
  STAGED_NOTE="NOTE: I could not read your composer before clearing — no prompt row in the capture. Do NOT read this as 'nothing was staged'."
fi

# RESET_DRYRUN — evaluate every gate above and exit WITHOUT clearing, so readiness can be
# checked before firing. reset_cai.sh / reset_fleet_health.sh / reset_nazim.sh have honored
# this since they were hardened; reset_lane.sh, reset_orch.sh and reset_hub_remote.sh did
# NOT, and ignored it SILENTLY — a dry run on a lane cleared it for real. orch-console hit
# exactly that on 2026-08-15 recycling cc-quality: the "dry run" wiped the lane, and only
# the fact that the lane had already confirmed it was safe to clear kept it from being a
# real loss. Same class as the MUSA_TELEGRAM_ID leg in nazim_send.sh (2026-07-26, which
# paged the operator from a test run): an env var that LOOKS like it disarms something must
# actually disarm it, and a safeguard that is silently absent is worse than one that was
# never offered — the caller believes they are protected. Placed AFTER the gates and BEFORE
# the first mutation so a dry run exercises exactly what a real run would.
if [ "${RESET_DRYRUN:-0}" = 1 ]; then
  echo "[reset_lane] RESET_DRYRUN=1 — gates evaluated (has-session/self-fire/busy/composer) on '$SESS', NOT clearing. Exiting."
  exit 0
fi

WIPE=$(( ${CC_BYTES:-0} + 80 )); [ "$WIPE" -lt 200 ] && WIPE=200; [ "$WIPE" -gt 20000 ] && WIPE=20000
echo "[reset_lane] clearing composer (${WIPE} BSpace) + /clear on '$SESS' ..."
"$TM" send-keys -t "$PANE" -N "$WIPE" BSpace; sleep 1

composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ]; then
  echo "ERROR: composer NOT empty after wipe — refusing to /clear into dirty input. Residue: ${CC_FLAT}" >&2
  echo "       Lane UNCHANGED, still holds its context. Staged text already preserved above." >&2
  exit 6
fi

"$TM" send-keys -t "$PANE" -l "/clear"; sleep 1
"$TM" send-keys -t "$PANE" Enter; sleep 4

BOOT="${BOOT_IN} ${STAGED_NOTE}"
echo "[reset_lane] booting '$SESS' ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"; sleep 1
"$TM" send-keys -t "$PANE" Enter; sleep 3
echo "[reset_lane] done. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -5
