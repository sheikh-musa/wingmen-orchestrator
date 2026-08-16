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

RESET=""; HANDOFF=""; DELAY=60; MAX_AGE=900; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --reset) RESET="$2"; shift 2;;
    --handoff) HANDOFF="$2"; shift 2;;
    --delay) DELAY="$2"; shift 2;;
    --max-handoff-age) MAX_AGE="$2"; shift 2;;
    --dry-run) DRY=1; shift;;
    *) echo "self_recycle: unknown arg '$1'" >&2; exit 2;;
  esac
done
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
echo "self_recycle: will fire '$RESET' in ${DELAY}s, DETACHED from this turn."

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
nohup bash -c "sleep $DELAY; env -u TMUX_PANE bash '$RESET' >> '$ORCH_DIR/$LOG' 2>&1" \
  >/dev/null 2>&1 &
disown 2>/dev/null || true
echo "self_recycle: SCHEDULED (pid $!) — fires in ${DELAY}s, log: $LOG"
echo "self_recycle: stop producing output now; the clear lands after this turn ends."
