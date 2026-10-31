#!/usr/bin/env bash
# reset_auditor.sh — in-place /clear + reboot-from-handoff of an AUDITOR SINGLETON
# (cc-quality / cc-storefront / cc-finance). CAI-1392 C.
#
# Usage:  scripts/reset_auditor.sh <quality|storefront|finance>      (cc- prefix accepted)
#         RESET_DRYRUN=1 ...   evaluate every gate, clear nothing (no arm needed)
#         scripts/reset_auditor.sh quality --arm-row N   console-executed cc-quality
#           self-arm path (see CC-QUALITY SELF-ARM PATH below); storefront/finance
#           reject --arm-row outright, tty self-fire only.
#
# WHY THIS EXISTS. The three auditors are CAI-500 singletons (fhb.SINGLETON_BODIES,
# CAI-1392 A, commit b7fb97f), so the worker recycler (sre_lane_recycle.py) refuses them by
# design — they were meant to recycle via their OWN reset, "once armed, CAI-1392 C", and that
# reset was never built. On 2026-10-02 cc-storefront sat idle at ~832k with a fresh handoff
# written and no sanctioned way to recycle it (orch-console #49338 -> SRE blocker #49348 ->
# decision #49350). This is that reset.
#
# IN-PLACE, NEVER KILL+RELAUNCH. A /clear keeps the pid, so the body keeps the token and the
# model it was launched with — cc-storefront is the Musa-token, opus-4-8-clamped money auditor
# (CAI-1170), and a relaunch would re-resolve both. Nothing here kills, respawns or relaunches.
#
# GATES, in order, all fail-closed, all BEFORE the first keystroke:
#   allowlist (2) -> has-session (1) -> caller-identity (5) -> busy (5) -> fresh handoff (3)
#   -> queued composer (7) -> [RESET_DRYRUN stops here, exit 0] -> ARMED (4)
#   -> pre-clear audit row (10)
#
# CALLER-IDENTITY (CAI-RESP-1442): this script fires ONLY when invoked by the TARGET's
# own live process (a detached runner it spawns itself) -- never by cc-fleet-health or
# any other caller. #37396(i)/CAI-RESP-501 hold singleton-reset authority to the
# singleton itself; the SRE detects + coordinates, it does not hold the authority to
# fire this on someone else's behalf. See gate 3 below for the mechanism.
# then the reset_cai.sh fire sequence: fire-window hold, composer capture+preserve, sized
# wipe with the ghost rule, /clear, LAYER-2 dead-man verify (8, escalates LOUD), boot.
#
# CC-QUALITY SELF-ARM PATH (Musa op_msg 35891, ask 5849; orch-console #83292/#83304;
# CAI-RESP-1451): cc-quality is an agent process with no real controlling tty of its own
# to share with a detached runner (CAI-RESP-1451's "unsatisfiable" finding), so the tty
# path above can never pass for it. --arm-row N is a SECOND way through THIS SAME gate,
# scoped to cc-quality ONLY (storefront/finance never reach this branch):
#   1. the caller must BE the console body: ORCH_BODY_ROLE=console and NO CC_BASE_AGENT_ID
#      set (checked here, in bash, independent of anything the row says -- CAI-RESP-1451's
#      crux, per cc-quality's own review point (b) in #83301: a row is NECESSARY but NOT
#      SUFFICIENT, so cc-quality can never self-fire even holding a genuine row).
#   2. a READ-ONLY check (scripts/lib/reset_auditor_arm_verify.py --mode check) that the
#      row id=N is: from_agent=cc-quality, to_agent=orch-console, message_type='decision',
#      subject starting with the literal "SELF-RESET-REQUEST: cc-quality" marker, created
#      within the last 30 minutes, and not already consumed. This is what decides THIS
#      gate (pass/fail) -- it never writes, so it is safe under RESET_DRYRUN too.
# The ACTUAL single-use consume (--mode consume, an atomic UPDATE...RETURNING) happens
# separately, much later (section 7.5, right before the pre-clear audit row) -- never here
# at gate 3 -- so a later gate failure (busy/stale handoff/queued/disarmed) or a dry run
# never burns a genuine request (orch-console #83304's ordering correction). NOBODY may
# reply-to or otherwise stamp responded_at on an arm row before THIS script consumes it,
# orch-console included -- every accidental stamp just fails closed (re-post the request).
#
# STANDING ARMED (cai's arm-sign, 2026-10-02, bus #49648): CAI-RESP-1442's withheld
# condition closed on PR #268 (caller-identity gate, cc-quality PASS + cai's own
# independent review of the diff) — ARMED_DEFAULT flipped below by this commit, per
# the SRE-owns-commit-of-approved-arming-edits process (the approval is cai's/Nazim's;
# committing the mechanical flip is ops). RESET_AUDITOR_ARMED=0 force-disarms one run
# (the deliberate-future-disarm path, same shape as sre_lane_recycle.py's arm bit).
#
# The target pane is derived from the allowlist ONLY — there is deliberately no session
# override (self_recycle.sh's --session skipped its allowlist; that was the hole).
set -uo pipefail
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
TM="${TM:-/usr/local/bin/tmux}"
[ -x "$TM" ] || TM="$(command -v tmux || echo /usr/local/bin/tmux)"

_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB="$_SCRIPTS_DIR/lib"
ORCH_DIR="${ORCH_DIR:-$(cd "$_SCRIPTS_DIR/.." && pwd)}"
PY="$ORCH_DIR/.venv/bin/python3"; [ -x "$PY" ] || PY="$HOME/wingmen/orchestrator/.venv/bin/python3"
[ -x "$PY" ] || PY="python3"
# shellcheck source=lib/composer_capture.sh
. "$_LIB/composer_capture.sh" || { echo "ERROR: composer_capture.sh missing" >&2; exit 9; }

# SETTLE WAITS ARE INJECTABLE (CI cost, orch-console #88092). Each `settle N` below
# pauses so the LIVE tmux TUI can repaint after a keystroke before the next one or
# before the capture that verifies it; the test STUB returns a fixture instantly, so
# under test those pauses are pure dead wall-clock (~10s per happy-path run, and this
# file's happy path runs 10x in CI). RESET_AUDITOR_SETTLE_S overrides ALL of them at
# once with one value; when it is unset every site keeps its historical N, so live
# timing is unchanged. Tests set 0 — the keystroke sequence they assert is identical.
settle() { sleep "${RESET_AUDITOR_SETTLE_S:-$1}"; }

ARMED_DEFAULT=1   # STANDING ARMED since cai's arm-sign (bus #49648, 2026-10-02, CAI-RESP-1442 closed)
REPORTS_DIR="${RESET_AUDITOR_REPORTS_DIR:-$ORCH_DIR/reports}"
MAX_AGE="${RESET_AUDITOR_HANDOFF_MAX_AGE:-900}"
LOGDIR="${RESET_AUDITOR_LOGDIR:-$ORCH_DIR/logs}"
RESET_BY="${RESET_BY:-cc-fleet-health}"
FORCED_GATES=""; BUSY_REASON=""   # recorded in the audit row (orch-console #49369)
ARM_VERIFY_CMD="${RESET_AUDITOR_ARM_VERIFY_CMD:-$PY $_LIB/reset_auditor_arm_verify.py}"
ARM_ROW_ID=""; _TARGET_ARG=""

# ── 0. ARG PARSE — pull --arm-row N out of argv, leaving the positional
#        target untouched (any position; no other flags exist here) ────────
while [ $# -gt 0 ]; do
  case "$1" in
    --arm-row) ARM_ROW_ID="${2:-}"; shift 2 ;;
    --arm-row=*) ARM_ROW_ID="${1#--arm-row=}"; shift ;;
    *) _TARGET_ARG="$1"; shift ;;
  esac
done

# ── 1. ALLOWLIST ─────────────────────────────────────────────────────────────
case "$_TARGET_ARG" in
  quality|cc-quality)       SHORT="quality" ;;
  storefront|cc-storefront) SHORT="storefront" ;;
  finance|cc-finance)       SHORT="finance" ;;
  *) echo "ERROR: reset_auditor.sh recycles ONLY the auditor singletons (quality|storefront|finance); got '${_TARGET_ARG}'. Workers use sre_lane_recycle.py; other singletons their own reset_<name>.sh." >&2; exit 2 ;;
esac
BASE="cc-$SHORT"; SESS="$SHORT"; PANE="${SESS}:0.0"
if [ -n "$ARM_ROW_ID" ] && [ "$SHORT" != "quality" ]; then
  echo "ERROR: --arm-row is scoped to cc-quality ONLY; got target '$SHORT'. storefront/finance use the tty self-fire path." >&2
  exit 2
fi

# ── 2. HAS-SESSION ───────────────────────────────────────────────────────────
"$TM" has-session -t "=$SESS" 2>/dev/null || { echo "ERROR: tmux session '$SESS' not found on this host." >&2; exit 1; }

# ── 3. CALLER-IDENTITY GUARD (CAI-RESP-1442, supersedes the old session-based
#        self-fire guard) ────────────────────────────────────────────────────
# Only the TARGET's own live process may fire this -- enforced from real OS process
# state, never a passed-in flag or any value a non-target process could set (the
# exact property cai named: not whether an argument SAYS "the target asked," but
# whether the caller actually IS the target's own process).
#
# Why TTY, not PPID (ancestry does not work here): the only way for a target to
# actually DETACH (a synchronous call would just deadlock the busy gate below, since
# the live tool call itself would keep the pane showing "esc to interrupt" for the
# whole run) is to background a runner from inside a live Bash tool call. That tool
# call's own subprocess exits almost immediately once the backgrounding returns,
# orphaning the runner -- by the time it actually fires, `ps` reports its ppid as 1
# (reparented), so walking ppid upward finds nothing of the target. Verified directly
# (nohup a job from inside a real tmux pane, forced the spawning shell to exit before
# checking): ppid -> 1, but the runner's controlling TTY is unchanged -- nohup does
# not call setsid, so it never detaches from the pane's pty. TTY survives reparenting;
# PPID does not. Each tmux pane owns a distinct pty, so an unrelated caller (e.g.
# cc-fleet-health, in its own separate pane) has a categorically different tty it
# cannot forge -- this is an OS fact read fresh, not a value passed in.
if [ -n "$ARM_ROW_ID" ]; then
  # ── 3b. CC-QUALITY SELF-ARM PATH (skips the tty check entirely) ───────────
  # (i) the caller must BE the console body -- checked here in bash, from real
  # process env, independent of anything the row says (CAI-RESP-1451 crux:
  # necessary but not sufficient -- cc-quality can never self-fire even with
  # a genuine row, since IT is never the console body).
  if [ "${ORCH_BODY_ROLE:-}" != "console" ] || [ -n "${CC_BASE_AGENT_ID:-}" ]; then
    echo "[reset_auditor] CALLER-IDENTITY REFUSED: --arm-row requires the caller to BE the console body (ORCH_BODY_ROLE=console, no CC_BASE_AGENT_ID) -- got ORCH_BODY_ROLE='${ORCH_BODY_ROLE:-}' CC_BASE_AGENT_ID='${CC_BASE_AGENT_ID:-}'. A row alone, even a genuine one, never arms this (CAI-RESP-1451)." >&2
    exit 5
  fi
  # ATTRIBUTION FIX (orch-console #83363, after bus #83362's misattribution):
  # the caller is now PROVEN to be the console body -- attribute every
  # downstream mention (the audit row's --by, its from_agent==to_agent self-
  # addressed pair, the LAYER-2-fail escalation sender, the boot message's
  # "reset IN-PLACE by ...") to orch-console, UNCONDITIONALLY -- not an
  # env-overridable default like the tty path's RESET_BY, since fleet_health_
  # boundaries.assert_no_sre_red_reset's whole point is that the SRE itself
  # must never appear as having fired a singleton reset it has no authority
  # to fire. This line must stay AFTER the console-caller check above, never
  # before it (attribution follows proof, not the other way round).
  RESET_BY="orch-console"
  # (ii) read-only check (never writes -- safe under RESET_DRYRUN too). This
  # is what decides gate 3; the actual single-use consume happens much later,
  # right before the audit row (section 7.5), never here.
  _arm_check_out="$($ARM_VERIFY_CMD --mode check --arm-row "$ARM_ROW_ID" --target "$SHORT" 2>&1)"
  _arm_check_rc=$?
  if [ "$_arm_check_rc" -ne 0 ]; then
    echo "[reset_auditor] CALLER-IDENTITY REFUSED: arm-row $ARM_ROW_ID did not check out -- $_arm_check_out" >&2
    exit 5
  fi
  echo "[reset_auditor] arm-row $ARM_ROW_ID checks out: $_arm_check_out"
else
  # ── 3a. TTY SELF-FIRE PATH (CAI-RESP-1442, supersedes the old session-based
  #        self-fire guard) ─────────────────────────────────────────────────
  # Only the TARGET's own live process may fire this -- enforced from real OS process
  # state, never a passed-in flag or any value a non-target process could set (the
  # exact property cai named: not whether an argument SAYS "the target asked," but
  # whether the caller actually IS the target's own process).
  #
  # Why TTY, not PPID (ancestry does not work here): the only way for a target to
  # actually DETACH (a synchronous call would just deadlock the busy gate below, since
  # the live tool call itself would keep the pane showing "esc to interrupt" for the
  # whole run) is to background a runner from inside a live Bash tool call. That tool
  # call's own subprocess exits almost immediately once the backgrounding returns,
  # orphaning the runner -- by the time it actually fires, `ps` reports its ppid as 1
  # (reparented), so walking ppid upward finds nothing of the target. Verified directly
  # (nohup a job from inside a real tmux pane, forced the spawning shell to exit before
  # checking): ppid -> 1, but the runner's controlling TTY is unchanged -- nohup does
  # not call setsid, so it never detaches from the pane's pty. TTY survives reparenting;
  # PPID does not. Each tmux pane owns a distinct pty, so an unrelated caller (e.g.
  # cc-fleet-health, in its own separate pane) has a categorically different tty it
  # cannot forge -- this is an OS fact read fresh, not a value passed in.
  _tty_of() {  # pid role -> tty ("??"/empty = unknown/no controlling terminal).
               # Stubbable (RESET_AUDITOR_TTY_STUB) for tests, which have no real tty.
    if [ -n "${RESET_AUDITOR_TTY_STUB:-}" ]; then "$RESET_AUDITOR_TTY_STUB" "$1" "$2"; return; fi
    ps -o tty= -p "$1" 2>/dev/null | tr -d ' '
  }
  _target_pane_pid="$("$TM" list-panes -t "=$SESS:0.0" -F '#{pane_pid}' 2>/dev/null)"
  if [ -z "$_target_pane_pid" ]; then
    echo "[reset_auditor] CALLER-IDENTITY REFUSED: cannot resolve $BASE's own pane pid -- refusing to verify caller identity (fail closed)." >&2
    exit 5
  fi
  _target_tty="$(_tty_of "$_target_pane_pid" target)"
  _caller_tty="$(_tty_of "$$" caller)"
  if [ -z "$_target_tty" ] || [ "$_target_tty" = "??" ] || [ -z "$_caller_tty" ] || [ "$_caller_tty" = "??" ] || [ "$_target_tty" != "$_caller_tty" ]; then
    echo "[reset_auditor] CALLER-IDENTITY REFUSED: this process's controlling tty ('${_caller_tty:-none}') is not $BASE's own pane tty ('${_target_tty:-unknown}') — only $BASE's own live process may fire its reset. Have $BASE spawn its own detached runner, e.g. from inside its own turn: nohup bash -c 'sleep N; exec $0 $SHORT' >/tmp/${SHORT}_reset_runner.log 2>&1 & disown" >&2
    exit 5
  fi
fi

# ── 4. BUSY GATE (shared pane_busy — one definition across the reset family) ─
pane_busy "$TM" "$PANE"
if [ "${CC_BUSY_STALE:-0}" = 1 ]; then
  echo "WARNING: $BASE showed a background-agent marker but the pane is FROZEN — treating as NOT busy (a live wait animates)." >&2
fi
if [ "$CC_BUSY" = 1 ]; then
  if [ "${RESET_FORCE:-0}" = "1" ]; then
    echo "WARNING: $BASE is BUSY — $CC_BUSY_REASON — RESET_FORCE=1, clearing ANYWAY (in-flight work DISCARDED)." >&2
    FORCED_GATES="busy"; BUSY_REASON="$CC_BUSY_REASON"
  else
    echo "ERROR: $BASE is BUSY — $CC_BUSY_REASON — refusing to clear. RESET_FORCE=1 overrides (loud)." >&2
    exit 5
  fi
fi

# ── 5. FRESH-HANDOFF GATE (shared finder; target mtime, any case, cc- or bare) ─
_hf="$("$PY" "$_LIB/handoff_find.py" --reports "$REPORTS_DIR" --base "$BASE" 2>/dev/null)" || _hf=""
if [ -z "$_hf" ]; then
  echo "ERROR: no handoff for $BASE in $REPORTS_DIR ([cc-]$SHORT-handoff-*.md) — refusing to clear (the reset would lose its state)." >&2
  exit 3
fi
HANDOFF="${_hf%%	*}"; _hmtime="${_hf##*	}"
_age=$(( $(date +%s) - _hmtime ))
if [ "$_age" -gt "$MAX_AGE" ]; then
  echo "ERROR: handoff $HANDOFF is ${_age}s old (max ${MAX_AGE}s) — refusing to clear onto a possibly-stale restore point. Have $BASE refresh it, then re-run." >&2
  exit 3
fi
echo "[reset_auditor] handoff OK: $HANDOFF (${_age}s old)"

# ── 6. QUEUED-COMPOSER GATE (op#11594) ───────────────────────────────────────
if "$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep "Press up to edit queued messages" >/dev/null; then
  echo "[reset_auditor] QUEUED-COMPOSER GATE: FAIL — '$SESS' has a queued message (would jam the /clear)." >&2
  if [ "${RESET_FORCE:-0}" != 1 ]; then echo "REFUSING /clear (RESET_FORCE=1 to override)." >&2; exit 7; fi
  echo "[reset_auditor] RESET_FORCE=1 — proceeding despite queued composer." >&2
  FORCED_GATES="${FORCED_GATES:+$FORCED_GATES,}queued"
fi

# ── RESET_DRYRUN — every gate above evaluated, nothing mutated ───────────────
if [ "${RESET_DRYRUN:-0}" = 1 ]; then
  echo "[reset_auditor] RESET_DRYRUN=1 — gates PASS for $BASE (allowlist/session/self-fire/busy/handoff/queued); would boot from $HANDOFF. NOT clearing."
  exit 0
fi

# ── 7. ARM GATE — standing armed since cai's sign (bus #49648), force-disarmable ──
# Comes AFTER the FORCE overrides above on purpose: RESET_FORCE only ever turns a gate's
# refusal into a recorded override — it can never reach the keystrokes of a disarmed run.
if [ "${RESET_AUDITOR_ARMED:-$ARMED_DEFAULT}" != 1 ]; then
  echo "[reset_auditor] DISARMED for this run (RESET_AUDITOR_ARMED=0 explicitly set). Nothing cleared. Gates otherwise PASS; RESET_DRYRUN=1 shows them." >&2
  exit 4
fi

# ── 7.5. ARM-ROW CONSUME — the ACTUAL single-use write, only now (after every
#        other gate incl. ARMED has passed), immediately before the audit row.
#        Zero rows back here refuses even though gate 3's check already passed
#        (closes the check-to-fire race; orch-console #83304's ordering fix).
if [ -n "$ARM_ROW_ID" ]; then
  _arm_consume_out="$($ARM_VERIFY_CMD --mode consume --arm-row "$ARM_ROW_ID" --target "$SHORT" 2>&1)"
  if [ $? -ne 0 ]; then
    echo "ERROR: arm-row $ARM_ROW_ID failed to consume at fire time -- $_arm_consume_out -- ABORTING. $BASE is untouched." >&2
    exit 5
  fi
  echo "[reset_auditor] $_arm_consume_out"
fi

# ── 8. PRE-CLEAR AUDIT ROW — no row, no /clear (dead-man's switch) ───────────
if [ -n "$ARM_ROW_ID" ]; then
  _reason="${RESET_REASON:-cc-quality self-arm reset (arm-row $ARM_ROW_ID, authority: Musa op_msg 35891)}"
else
  _reason="${RESET_REASON:-auditor recycle (bloat/idle seam)}"
fi
_audit_args=(--by "$RESET_BY" --base "$BASE" --session "$SESS" --handoff "$HANDOFF" --reason "$_reason"
             --forced-gates "${FORCED_GATES:-none}" --busy-reason "$BUSY_REASON")
if [ -n "${RESET_AUDITOR_AUDIT_CMD:-}" ]; then
  "$RESET_AUDITOR_AUDIT_CMD" "${_audit_args[@]}"
else
  "$PY" "$_LIB/reset_audit_row.py" "${_audit_args[@]}"
fi
if [ $? -ne 0 ]; then
  echo "ERROR: could NOT write the pre-clear audit row — ABORTING. $BASE is untouched and still holds its context." >&2
  exit 10
fi

# ── FIRE ─────────────────────────────────────────────────────────────────────
# FIRE-WINDOW HOLD: every keystroke sender on this host stands off for the window;
# self-expiring and released on EXIT so a crashed reset never leaves the body unreachable.
. "$_LIB/fire_window.sh"
fire_window_hold "$SESS" 180 "reset_auditor fire window"

# Capture + preserve the composer before the wipe destroys it.
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ]; then
  mkdir -p "$LOGDIR"
  printf '%s reset_auditor[%s] staged: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SESS" "$CC_FLAT" >> "$LOGDIR/reset_auditor_preserved_input.log"
  echo "[reset_auditor] PRESERVED staged composer: $CC_FLAT"
  STAGED_NOTE="NOTE: you had \"${CC_FLAT}\" staged UNSENT when I cleared you (captured verbatim to logs/reset_auditor_preserved_input.log). Judge whether it was your own next step or an instruction typed at your pane and never carried out; it is yours to re-decide."
elif [ "${CC_PARTIAL:-}" = 'noprompt' ]; then
  STAGED_NOTE="NOTE: I could NOT read your composer before clearing you — no prompt row in the capture. Do NOT read this as 'nothing was staged'."
else
  STAGED_NOTE="NOTE: your composer was EMPTY when I cleared you — nothing staged, nothing lost."
fi
[ "${CC_GHOST:-0}" = 1 ] && STAGED_NOTE="${STAGED_NOTE} (auto-classified a history-GHOST of a prior submit — most likely NOT real staged work; preserved regardless.)"

WIPE=$(( ${CC_BYTES:-0} + 80 )); [ "$WIPE" -lt 200 ] && WIPE=200; [ "$WIPE" -gt 20000 ] && WIPE=20000
CC_BEFORE_WIPE="$CC_FLAT"   # the wipe is also the ghost probe — keep its input
echo "[reset_auditor] clearing composer (${WIPE} BSpace) + /clear on '$SESS' ..."
"$TM" send-keys -t "$PANE" -N "$WIPE" BSpace; settle 1

composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ] && [ "${CC_GHOST:-0}" != 1 ]; then
  # GHOST RULE: real text cannot survive $WIPE backspaces byte-identical, so unchanged
  # residue is a dim ghost in an empty buffer — proceed. CHANGED residue is a real partial
  # wipe — /clear would stage behind it — refuse.
  if [ "$CC_FLAT" = "$CC_BEFORE_WIPE" ]; then
    echo "[reset_auditor] GHOST: composer byte-identical after ${WIPE} BSpace — empty underneath. Proceeding." >&2
  else
    echo "ERROR: composer NOT empty after wipe — refusing to /clear into dirty input. Residue: ${CC_FLAT} (was '${CC_BEFORE_WIPE}')." >&2
    echo "       $BASE is UNCHANGED and still holds its context; staged text already preserved." >&2
    exit 6
  fi
fi

"$TM" send-keys -t "$PANE" -l "/clear"; settle 1
"$TM" send-keys -t "$PANE" Enter; settle 4

# LAYER-2 DEAD-MAN VERIFY: confirm the /clear actually ran before booting; a boot onto a
# still-bloated context is a false 'done'. Live composer empty on a readable capture, or the
# fresh-session banner (which a composer ghost can never fake).
_cleared=0
for _i in 1 2 3 4; do
  composer_parse_pane "$TM" "$PANE"
  if [ "$CC_EMPTY" = 1 ] && [ "$CC_PARTIAL" = 'ok' ]; then _cleared=1; break; fi
  if "$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep -E 'Claude Code v[0-9]' >/dev/null; then _cleared=1; break; fi
  settle 2
done
if [ "$_cleared" != 1 ]; then
  echo "[reset_auditor] LAYER-2 VERIFY: FAIL — /clear did NOT execute on $BASE. NOT sending boot." >&2
  # Musa op#34845, bus #78757/#78763 (PR-B step 2): was a raw psycopg INSERT INTO
  # agent_messages; routed through scripts/bus_send.py, same sanctioned path as
  # every other migrated writer.
  # Never post this operator page from a test (orch-console #80174, leaks #78935/#80158).
  # Fail CLOSED: if the guard can't even load, refuse rather than risk a test paging the operator.
  _esc_refused=''
  if ! . "$ORCH_DIR/scripts/lib/reset_escalation_guard.sh" 2>/dev/null; then
    _esc_refused="guard library missing ($ORCH_DIR/scripts/lib/reset_escalation_guard.sh)"
    echo "[reset_auditor] REFUSED to post the operator-relay row: $_esc_refused. NOTHING was posted." >&2
  elif reset_escalation_test_context; then
    _esc_refused="$RESET_ESC_TEST_REASON"
    reset_escalation_refuse reset_auditor "$_esc_refused"
  fi
  if [ -n "$_esc_refused" ]; then
    echo "[reset_auditor] recycle FAILED; the escalation was refused (test context), so nothing reached the operator." >&2
  elif "$PY" "$ORCH_DIR/scripts/bus_send.py" --to orch-console --from "$RESET_BY" \
       --type blocker --priority P1 --req \
       --subject "LOUD: reset_auditor $BASE STUCK — /clear did not take, recycle NOT done" \
       --body "reset_auditor.sh fired on $BASE but the /clear did not execute; NO boot was sent. The body is still on its old context. Re-fire on a clean composer." \
       >/dev/null 2>&1
  then echo "[reset_auditor] escalated LOUD failure to orch-console." >&2
  else echo "[reset_auditor] WARNING: could NOT write the escalation row — escalate MANUALLY. The recycle FAILED." >&2
  fi
  exit 8
fi
echo "[reset_auditor] LAYER-2 VERIFY: PASS — /clear executed."

BOOT="You are $BASE, an auditor singleton (CAI-500), freshly reset IN-PLACE by ${RESET_BY} at $(date -u +%Y-%m-%dT%H:%MZ) via reset_auditor.sh — same process, so your token and model are unchanged. Read your handoff at ${HANDOFF} IN FULL FIRST (absolute path — not your cwd's reports/); it is the authority over this message. Then reconcile agent_messages where to_agent='${BASE}' (and your instance id) and read_at is null; re-claim any open review it lists before taking new work. DB: source ~/wingmen/orchestrator/.env (file-first) before any DB command — your process env may hold a pre-rotation DSN. ${STAGED_NOTE} STANDING: verify-not-assert; a measurement whose tooling failed reports 'could not measure', never a finding; when a premise falls, RE-DERIVE. Reply to orch-console once you are up."
echo "[reset_auditor] booting '$SESS' ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"; settle 1
"$TM" send-keys -t "$PANE" Enter; settle 3
echo "[reset_auditor] done — $BASE booting from $HANDOFF. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -5
