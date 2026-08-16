#!/usr/bin/env bash
# self_recycle.sh — a body recycles ITSELF, cleanly, without a human pushing a button.
#
# WHY THIS EXISTS. The operator has asked for self-recycling bodies repeatedly — his words on
# 2026-08-16: "thats what ive been saying 1000 times and yet here i am telling you youre bloated
# and having to push a button." He was right, and the reason it had not been built was a
# misreading of the self-fire guard.
#
# THE GUARD IS ABOUT TIMING, NOT AUTHORITY. reset_*.sh refuses when invoked from INSIDE the target
# session because its send-keys would land in a terminal that is still running the caller's turn:
# the boot instruction can arrive before the /clear completes, and the body comes back
# half-initialised, holding neither the old context nor a clean one. That hazard is real. It is
# also entirely a function of WHEN the keystrokes land — not of who asked for them.
#
# So this does not evade the guard, it removes the hazard the guard exists for: the reset is
# DETACHED from the calling turn and DELAYED past its end, then runs as an ordinary external
# caller. By the time it fires, the turn it would have interleaved with is over.
#
# WHY IT NEEDS NO ARM-SIGN, unlike Stage-2 auto-recycle. Every gate on the destructive fleet-wide
# recycler exists because one body would be clearing ANOTHER on an inference about its state —
# "it looks idle", "it says it's done" — and on 2026-08-15 three of four such inferences were
# wrong. None of that applies here. A body clearing ITSELF, on ITS OWN freshly-written handoff,
# is not acting on an inference: it is the only party that knows whether it is finished, and it
# bears the entire cost of being wrong. That is why this is a different thing wearing a similar
# shape.
#
# THE ONE REAL RISK is recycling onto a stale restore point — the failure that made three of three
# handoffs untrustworthy that night. So freshness is a HARD precondition here, checked against the
# clock at fire time, not asserted by the caller.
#
# Usage (from inside the body's own session):
#   scripts/self_recycle.sh --reset scripts/reset_nazim.sh --handoff reports/nazim-handoff-NOW.md \
#                           [--delay 60] [--max-handoff-age 900] [--dry-run]
set -uo pipefail
ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ORCH_DIR" || { echo "self_recycle: orch dir missing" >&2; exit 9; }

RESET=""; HANDOFF=""; DELAY=60; MAX_AGE=900; DRY=0; SESSION=""; MAX_WAIT=900
while [ $# -gt 0 ]; do
  case "$1" in
    --reset) RESET="$2"; shift 2;;
    --handoff) HANDOFF="$2"; shift 2;;
    --delay) DELAY="$2"; shift 2;;
    --max-handoff-age) MAX_AGE="$2"; shift 2;;
    --session) SESSION="$2"; shift 2;;
    --max-wait) MAX_WAIT="$2"; shift 2;;
    --dry-run) DRY=1; shift;;
    *) echo "self_recycle: unknown arg '$1'" >&2; exit 2;;
  esac
done

# WHICH PANE THIS ACTS ON. Derived from the reset script, because each reset hardcodes its own
# target — but NEVER guessed: reset_lane.sh takes its session as an argument, so there is nothing
# to derive and a guess would aim a /clear at whatever happened to match. Refuse instead.
if [ -z "$SESSION" ]; then
  case "$(basename "$RESET")" in
    reset_nazim.sh)        SESSION="${ORCH_TMUX_SESSION:-nazim}";;
    reset_cai.sh)          SESSION="cai";;
    reset_fleet_health.sh) SESSION="fleet-health";;
    reset_orch.sh)         SESSION="${ORCH_TMUX_SESSION:-orch}";;
    *) echo "self_recycle: cannot derive the target session from '$(basename "$RESET")' — pass --session <tmux-session>." >&2; exit 2;;
  esac
fi
[ -n "$RESET" ]   || { echo "self_recycle: --reset required" >&2; exit 2; }
[ -n "$HANDOFF" ] || { echo "self_recycle: --handoff required" >&2; exit 2; }
[ -x "$RESET" ] || [ -f "$RESET" ] || { echo "self_recycle: reset script not found: $RESET" >&2; exit 2; }

# GATE 1 — THE RESTORE POINT MUST BE FRESH. This is the one that matters. Recycling onto a stale
# handoff does not preserve the work, it launders the loss: the body comes back confident and
# wrong and nobody can tell what went. Measured against the clock now, never asserted by a caller.
if [ ! -f "$HANDOFF" ]; then
  echo "self_recycle: REFUSED — handoff does not exist: $HANDOFF" >&2; exit 3
fi
NOW=$(date +%s)
MTIME=$(stat -f %m "$HANDOFF" 2>/dev/null || stat -c %Y "$HANDOFF" 2>/dev/null)
AGE=$(( NOW - MTIME ))
if [ "$AGE" -gt "$MAX_AGE" ]; then
  echo "self_recycle: REFUSED — handoff is ${AGE}s old (max ${MAX_AGE}s). Write a fresh one FIRST." >&2
  echo "             A recycle onto a stale restore point loses work and looks deliberate." >&2
  exit 4
fi

# GATE 2 — it must be a real restore point, not a stub. Deliberately a floor, not a quality bar;
# length is a poor proxy for quality and the honest checks live in scripts/lib/handoff_verify.py.
BYTES=$(wc -c < "$HANDOFF" | tr -d ' ')
if [ "$BYTES" -lt 800 ]; then
  echo "self_recycle: REFUSED — handoff is only ${BYTES}B; that is a note, not a restore point." >&2
  exit 5
fi

echo "self_recycle: handoff OK (${BYTES}B, ${AGE}s old)"
echo "self_recycle: target session: ${SESSION}"
echo "self_recycle: will quiesce '${SESSION}', wait for it to go idle (up to ${MAX_WAIT}s), then fire '$RESET' — DETACHED from this turn."

if [ "$DRY" = 1 ]; then
  echo "self_recycle: --dry-run — gates passed, NOTHING scheduled."; exit 0
fi

mkdir -p logs
# DETACH + DELAY is the whole safety property, so be explicit about each piece:
#   sleep $DELAY   — lets the calling turn finish; the interleave hazard is a same-turn hazard.
#   env -u TMUX_PANE — the reset's self-fire guard keys off an inherited $TMUX_PANE. Unsetting it
#                    is honest here rather than evasive: by the time this runs the caller's turn
#                    is over, so it genuinely IS an external invocation, which is the condition
#                    the guard is actually testing for.
#   setsid/nohup   — survives the calling shell so the reset cannot die with the turn that asked
#                    for it, which would leave the body bloated and believing it had recycled.
LOG="logs/self_recycle_$(date -u +%Y%m%dT%H%M%SZ).log"

# WAIT FOR IDLE, DO NOT FIRE BLIND (2026-08-16, cai's first real attempt). The detach worked and
# the process survived, but at T+DELAY a `[wake] new inbox item` had put the body mid-turn and the
# reset's BUSY gate correctly refused. The tool was fine and the gate was right; the SHAPE was
# wrong. A one-shot assumes the body is idle at exactly T+DELAY, and a body on a live bus is woken
# precisely BECAUSE it is the kind of body worth recycling. The alternative offered — "recycle me
# externally from an idle moment" — puts the operator's thumb back on the button, which is the
# whole thing this line of work exists to remove.
#
# The two halves compose and neither alone is enough:
#   QUIESCE FIRST — take the fire-window hold for the whole wait, so the nudgers stand off and
#   nothing NEW can start a turn while we wait for the current one to end. reset_*.sh takes its own
#   hold, but only AFTER its busy gate, which is already too late.
#   THEN WAIT — poll the pane for the busy marker and fire on the first idle read, bounded by
#   MAX_WAIT so a genuinely stuck body fails LOUDLY instead of hanging forever.
# The hold is renewed each iteration (its TTL is deliberately short so a crash cannot leave a body
# quiesced) and released on every exit path, including the timeout.
nohup bash -c '
  ORCH="'"$ORCH_DIR"'"; SESS="'"$SESSION"'"; RST="'"$RESET"'"; LG="'"$ORCH_DIR/$LOG"'"
  DELAY='"$DELAY"'; MAX_WAIT='"$MAX_WAIT"'
  PY="$ORCH/.venv/bin/python3"; [ -x "$PY" ] || PY=python3
  FW="$ORCH/scripts/lib/fire_window.py"
  # Read the LIVE FOOTER, never the whole pane: "esc to interrupt" left in the scrollback from
  # an earlier turn made this loop read busy forever, and it stranded cai at 838k with an idle
  # composer while holding the lock that suppressed her wakes (2026-08-16, my bug).
  . "$ORCH/scripts/lib/pane_busy.sh"
  hold() { "$PY" "$FW" hold "$SESS" --ttl 240 --reason "self_recycle waiting for $SESS to go idle" >/dev/null 2>&1; }
  free() { "$PY" "$FW" release "$SESS" >/dev/null 2>&1; }
  trap free EXIT
  hold
  sleep "$DELAY"
  WAITED=0
  while :; do
    hold
    if ! pane_busy "$SESS"; then
      echo "[self_recycle] $SESS is idle after ${WAITED}s of waiting — firing $RST" >> "$LG"
      free
      env -u TMUX_PANE bash "$RST" >> "$LG" 2>&1
      exit 0
    fi
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
      echo "[self_recycle] GIVING UP: $SESS still busy after ${WAITED}s — NOT clearing a working body. Nothing was changed; re-run when it settles." >> "$LG"
      "$ORCH/scripts/nazim_send.sh" "⚠️ self_recycle gave up on \`$SESS\` — still mid-turn after ${WAITED}s, so nothing was cleared. The body is fine; it is just busy. Log: $LG" >/dev/null 2>&1 || true
      exit 1
    fi
    sleep 15; WAITED=$(( WAITED + 15 ))
  done
' >/dev/null 2>&1 &
disown 2>/dev/null || true
echo "self_recycle: SCHEDULED (pid $!) — quiescing '${SESSION}', firing on its first idle read after ${DELAY}s (giving up after ${MAX_WAIT}s), log: $LOG"
echo "self_recycle: stop producing output now; the clear lands after this turn ends."
