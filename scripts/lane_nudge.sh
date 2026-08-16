#!/usr/bin/env bash
# lane_nudge.sh — VERIFIED-SUBMIT wrapper for nudging a CC lane's tmux session.
#
# WHY: `tmux send-keys ... Enter` is unreliable — the Enter frequently fails to
# submit, leaving the lane IDLE with the prompt sitting unsent in its input box.
# This silently stalled lanes ~5x on 2026-06-20 (fleet drifted idle while the
# operator was engaged elsewhere). cai CAI-RESP-284 HARD RULE 1: a lane auto-nudge
# MUST NOT depend on a bare send-keys Enter — use a verified submit (confirm it
# actually submitted; clear+retype fallback). This is that wrapper.
#
# Usage:  lane_nudge.sh <tmux-session> "<message>"
# Exit:   0 = verified submitted (pane entered a working state)
#         3 = could not verify submission after retries (caller should escalate)
#         2 = usage / no such session
#
# Verification heuristic (matches the observable Claude-Code TUI states):
#   working  -> footer shows "esc to interrupt"   (submitted, lane is running)
#   idle     -> footer shows "for agents"          (NOT submitted / still idle)
set -uo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Shared, SGR-aware composer extraction — the fleet's ONE definition of "dim ghost
# vs real staged text" (reset_lane.sh sources the same lib; lane_wedge_watchdog.py
# mirrors its dim test). Used by the ghost-aware guard below so this wrapper never
# clobbers a lane's own genuinely-staged next-step, while still treating a dim
# autosuggestion ghost as the empty buffer it really is.
. "$ORCH_DIR/scripts/lib/composer_capture.sh" || { echo "lane_nudge: composer_capture.sh missing" >&2; exit 2; }

SESSION="${1:?usage: lane_nudge.sh <tmux-session> \"<message>\"}"
MSG="${2:?missing message}"
MAX_TRIES="${LANE_NUDGE_TRIES:-3}"
# Where diagnostic logs land. Defaults to the tree's logs/; overridable so a refusal's
# self-diagnosis can be relocated (and tested) without touching the live log stream.
LOGDIR="${LANE_NUDGE_LOG_DIR:-$ORCH_DIR/logs}"

tmux has-session -t "$SESSION" 2>/dev/null || { echo "lane_nudge: no tmux session '$SESSION'" >&2; exit 2; }

# FIRE-WINDOW GUARD. A recycle owns this pane for a few seconds while it wipes the
# composer, types /clear, submits it and types the boot instruction. A nudge landing
# inside that window jams the clear and the body returns half-initialised, holding
# neither its old context nor a clean one. Refuse rather than type — the caller's
# payload is a durable bus/operator row that the fresh body reconciles at boot, so a
# skipped nudge costs nothing. The hold is self-expiring, so a crashed resetter cannot
# leave a lane permanently unreachable. See scripts/lib/fire_window.py.
if "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/fire_window.py" check "$SESSION" 2>/dev/null; then
  echo "lane_nudge: REFUSED — '$SESSION' is inside a recycle fire window; not typing into a pane mid-clear." >&2
  exit 4
fi

# GHOST-AWARE COMPOSER GUARD (2026-07-29). The retry loop below CLEARS the composer
# (C-u ×2) before retyping — which would DESTROY any genuinely-staged next-step the
# lane typed for itself (a clobber-real-input violation). An IDLE Claude-Code lane
# also paints its most-recent history entry as a DIM (SGR-2) autosuggestion GHOST
# into an EMPTY input buffer; a plain capture-pane misreads that ghost as staged
# text. So read the composer with the shared SGR-aware extractor and REFUSE only
# when we POSITIVELY read REAL, non-dim staged text — preserving it — rather than
# clobber it. Empty / dim-ghost / placeholder / unreadable(noprompt) all PROCEED,
# exactly as before (so a fresh-boot pane, e.g. spawn_reviewer, is never blocked).
# Capture the composer bytes ONCE and parse THOSE SAME BYTES, so that if we refuse, the
# capture we log is provably the bytes the verdict was made on — no second capture, no
# moment-axis gap between "what the parser saw" and "what we logged" (#23648, the same-
# bytes method that made the shipforge repro definitive). This is byte-for-byte what
# composer_parse_pane does internally (capture -p -e, then -p fallback); we only keep the
# raw text around so the refuse branch below can persist it. The verdict is unchanged.
CC_RAWCAP="$(tmux capture-pane -t "$SESSION" -p -e 2>/dev/null)"
[ -n "$CC_RAWCAP" ] || CC_RAWCAP="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null)"
composer_parse "$CC_RAWCAP"
# SELF-DIAGNOSING capture log (Nazim #23572 / CAI-978), now shared by BOTH the refuse and the
# step-4 proceed-on-ghost paths: persist the RAW capture-pane -e bytes the verdict was made on
# (dim/non-dim markers intact), the parser's verdict fields, CC_PROBE, and pane geometry — at
# the instant of the verdict. $1 = a label (REFUSED / PROCEEDED-ghost / REFUSED-revert-fail).
# This IS the "first ~10 wired firings" corpus Nazim reads before signing the promotion.
_log_probe_capture() {
  local label="$1" stamp capname capdir geom capnote
  mkdir -p "$LOGDIR" 2>/dev/null || true
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  capname="${SESSION}-${stamp}.e.txt"
  capdir="$LOGDIR/lane_nudge_captures"
  geom="$(tmux display-message -p -t "$SESSION" '#{pane_width}x#{pane_height}' 2>/dev/null || echo '?x?')"
  if mkdir -p "$capdir" 2>/dev/null && printf '%s' "$CC_RAWCAP" > "$capdir/$capname" 2>/dev/null && [ -s "$capdir/$capname" ]; then
    capnote="raw=lane_nudge_captures/$capname"; CC_LAST_CAPFILE="lane_nudge_captures/$capname"
  else
    capnote="raw=CAPTURE-FAILED"; CC_LAST_CAPFILE=""   # fail-LOUD, never a silent gap — the miss is visible in the log line
  fi
  # Report the content the probe SAW (CC_PROBE_BEFORE), not CC_FLAT — the probe overwrites CC_FLAT
  # with its after/after-revert captures, so on ghost/revert-fail CC_FLAT is the wrong (empty) text.
  printf '%s lane_nudge[%s] %s: %s | verdict: CC_N=%s CC_EMPTY=%s CC_PARTIAL=%s CC_GHOST=%s basis=%s CC_PROBE=%s geom=%s %s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SESSION" "$label" "${CC_PROBE_BEFORE:-$CC_FLAT}" \
    "${CC_N:-?}" "${CC_EMPTY:-?}" "${CC_PARTIAL:-?}" "${CC_GHOST:-?}" "${CC_PH_BASIS:-?}" "${CC_PROBE:-}" "$geom" "$capnote" \
    >> "$LOGDIR/lane_nudge_preserved_input.log" 2>/dev/null || true
}

# Best-effort P1 escalation for a probe REVERT-FAIL (cond#2). The REFUSE (exit 3) happens
# REGARDLESS of whether this lands — safety never depends on the DB being reachable.
# Guards, each a real defect caught the first night this fired (#23895):
#   * NO DATABASE_URL -> skip (also keeps tests from ever writing to the live bus).
#   * pane GONE -> skip: a P1 saying "inspect the pane" is useless if the pane no longer exists
#     (and a nonexistent session only reaches here via a fake tmux, i.e. a test).
#   * DEDUPE on the rise (per session, 1h) -> a persistently-failing lane must not storm the bus
#     (the 2026-07-08 nudge-storm lesson, #23290).
# It CARRIES its evidence: the raw-capture pointer + the BEFORE content, so the reader can act.
_probe_p1_escalate() {   # $1 = capfile pointer (may be empty), $2 = before-flat
  local capref="${1:-}" beforeflat="${2:-}"
  [ -n "${DATABASE_URL:-}" ] || return 0
  tmux has-session -t "$SESSION" 2>/dev/null || return 0
  local dedupe="$LOGDIR/.probe_revertfail_${SESSION}.stamp"
  if [ -f "$dedupe" ]; then
    # skip if we escalated for this session within the last hour
    find "$dedupe" -mmin -60 2>/dev/null | grep -q . && return 0
  fi
  mkdir -p "$LOGDIR" 2>/dev/null && : > "$dedupe" 2>/dev/null || true
  local py="${ORCH_DIR}/.venv/bin/python3"; [ -x "$py" ] || py=python3
  "$py" - "$SESSION" "$beforeflat" "$capref" <<'PYEOF' 2>/dev/null || true
import os, sys
try:
    import psycopg2
    sess, flat, capref = sys.argv[1], sys.argv[2], sys.argv[3]
    ev = f"logs/{capref}" if capref else "NONE (capture also failed — inspect the live pane immediately)"
    c = psycopg2.connect(os.environ["DATABASE_URL"]); cur = c.cursor()
    cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response) "
        "VALUES ('cc-fleet-health','orch-console','blocker',%s,%s,true)",
        (f"⚠ P1 lane_nudge PROBE REVERT-FAIL on {sess} — composer not restored byte-identical; refused",
         f"The ghost-probe on '{sess}' typed its 1-byte sentinel but the BSpace did NOT restore the "
         f"composer byte-identical. The content at risk (before the probe): '{flat}'. A real staged "
         f"step MAY be corrupted; lane_nudge REFUSED (did not clear+deliver). "
         f"Raw capture: {ev}. Inspect the pane '{sess}'."))
    c.commit()
except Exception:
    pass
PYEOF
}

if [ "${CC_EMPTY:-0}" != 1 ] && [ "${CC_PARTIAL:-noprompt}" != 'noprompt' ] && [ "${CC_N:-0}" -gt 0 ] 2>/dev/null; then
  # STEP-4 (Nazim promotion, coupled behind the pane_busy collapse b5d82ce). The composer READS
  # as real staged text — but a dim AUTOSUGGESTION ghost parses identically (#23536), and at
  # steady state ~11/20 lanes hold one, each REFUSED forever (#23884, the self-poisoning loop).
  # Content can't tell them apart (the ghosts are plausible next steps) — only the MECHANICAL
  # probe can: type a 1-byte sentinel and see whether it REPLACED an empty buffer (ghost) or
  # APPENDED to real text (real). SAFE-FAILURE ASYMMETRY: every non-ghost verdict — real, unsure,
  # busy, locked, revert-fail, or an unset CC_PROBE — degrades to today's REFUSE. The probe can
  # only ADD deliveries on proven-empty composers; it can never clobber real staged work.
  _probe_composer tmux "$SESSION"
  case "${CC_PROBE:-}" in
    ghost)
      # Proven empty: the sentinel REPLACED the composer, so the dim text was a ghost, not content.
      # PROCEED to clear+deliver; record the decision + the raw before-capture for the promotion read.
      _log_probe_capture "PROCEEDED-ghost, cleared"
      echo "lane_nudge: '$SESSION' composer held a dim GHOST (probe: replaced by sentinel) — proceeding to deliver." >&2
      # fall through past this if-block to the delivery loop
      ;;
    revert-fail)
      # cond#2: the BSpace did NOT restore the composer byte-identical — a real staged step may be
      # corrupted. FAIL LOUD + escalate P1 + REFUSE. NEVER proceed.
      _log_probe_capture "REFUSED-revert-fail, preserved staged"
      _probe_p1_escalate "${CC_LAST_CAPFILE:-}" "${CC_PROBE_BEFORE:-}"
      echo "lane_nudge: REVERT-FAIL on '$SESSION' — composer NOT restored byte-identical after the probe; REFUSING + escalated P1 (possible corruption of a real staged step)." >&2
      exit 3
      ;;
    *)
      # real | unsure | busy | locked | '' (probe error): preserve + refuse, exactly as before step-4.
      _log_probe_capture "REFUSED, preserved staged"
      echo "lane_nudge: REFUSED — '$SESSION' composer holds REAL unsent text (probe: ${CC_PROBE:-unknown}); clearing+retyping would clobber the lane's own staged step." >&2
      echo "           Preserved verbatim to logs/lane_nudge_preserved_input.log (+ raw capture for diagnosis) — submit/escalate it by hand rather than nudging over it." >&2
      exit 3
      ;;
  esac
fi

pane_working() {
  # working iff the live footer shows the interrupt hint and NOT the idle hint
  local cap; cap="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -3)"
  printf '%s' "$cap" | grep -q 'esc to interrupt' && ! printf '%s' "$cap" | grep -q 'for agents'
}

pane_queued() {
  # A BUSY lane accepts the nudge into its message QUEUE — the TUI shows "Press up to edit
  # queued messages". That is a SUCCESSFUL delivery, but pane_working() can't see it (the
  # footer shows both hints at once), so the retry loop used to retype twice more and leave
  # three identical queued nudges behind. Observed 2026-07-25 on cosem-port, 34 min into a
  # task: one nudge became three. Delivery is the goal; queued IS delivered.
  tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -6 | grep -q 'queued message'
}

for try in $(seq 1 "$MAX_TRIES"); do
  # clear any stale/unsent input, then type fresh, then submit
  tmux send-keys -t "$SESSION" C-u; sleep 0.4
  tmux send-keys -t "$SESSION" C-u; sleep 0.4
  tmux send-keys -t "$SESSION" -l "$MSG"; sleep 1
  tmux send-keys -t "$SESSION" Enter; sleep 4
  if pane_working; then
    echo "lane_nudge: '$SESSION' submitted + working (try $try)"; exit 0
  fi
  if pane_queued; then
    echo "lane_nudge: '$SESSION' is BUSY — nudge accepted into its queue (try $try). Delivered; it reads at its next pause."
    exit 0
  fi
  # one extra Enter in case the TUI consumed the first as focus
  tmux send-keys -t "$SESSION" Enter; sleep 3
  if pane_working; then
    echo "lane_nudge: '$SESSION' submitted + working (try $try, 2nd Enter)"; exit 0
  fi
  if pane_queued; then
    echo "lane_nudge: '$SESSION' is BUSY — nudge accepted into its queue (try $try, 2nd Enter)."
    exit 0
  fi
  echo "lane_nudge: '$SESSION' not yet working after try $try — retrying" >&2
done

echo "lane_nudge: FAILED to verify submission to '$SESSION' after $MAX_TRIES tries — escalate (lane may be at a dialog/trust-prompt, or crashed)" >&2
exit 3
