#!/usr/bin/env bash
# reset_nazim.sh — in-place /clear + boot of the Nazim (orch-console) session.
# Run this FROM ANOTHER shell — the operator on the Mini, or the hub via
#   ssh Musa@sheikhs-mac-mini bash ~/wingmen/orchestrator/scripts/reset_nazim.sh
# It send-keys into tmux 'nazim'; it does NOT /clear the shell that runs it.
# Why in-place /clear (not kill): boot_nazim relaunches with --continue, which
# would reload the bloated conversation — same trap as the hub. The /clear is
# what actually frees the context; the boot instruction reloads from the handoff.
set -uo pipefail

# Resolve tmux robustly. Under launchd (the fleet-console runs the reset) PATH is
# /usr/bin:/bin:/usr/sbin:/sbin — NO /usr/local/bin — so `command -v tmux` finds
# nothing and the old bare /opt/homebrew fallback (absent on this Mini) made
# has-session fail -> the reset silently no-op'd with rc=1 (op#9393, 2026-08-02).
# Mini singleton sessions live on the /usr/local/bin/tmux socket (see
# reference_mini_tmux_two_binaries_socket); prefer it, mirror reset_lane.sh.
TM="${TM:-/usr/local/bin/tmux}"
[ -x "$TM" ] || TM="$(command -v tmux || echo /opt/homebrew/bin/tmux)"
SESS="nazim"
PANE="${SESS}:0.0"
# Newest handoff wins — a hardcoded filename rots the moment a new one is written, and a
# fresh body then boots from a stale board. (Found 2026-07-26: this still pointed at the
# MORNING handoff, so a reset would have sent fresh-Nazim to re-do the irsyad lane build
# that finished hours earlier. Same trap already fixed in reset_cai.sh and reset_orch.sh.)
HANDOFF="$(ls -t reports/nazim-handoff-*.md 2>/dev/null | head -1)"

# The fleet's ONE "what is staged in the live composer?" definition (dim-ghost vs
# real text) — same lib reset_cai.sh + reset_fleet_health.sh source. Used by the
# Layer-2 post-/clear verify below so it reads the LIVE composer, not scrollback.
_RESET_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/composer_capture.sh
. "$_RESET_LIB_DIR/composer_capture.sh" || { echo "ERROR: composer_capture.sh missing" >&2; exit 9; }

if ! "$TM" has-session -t "$SESS" 2>/dev/null; then
  echo "ERROR: tmux session '$SESS' not found on this host. Are you on the Mini?" >&2
  exit 1
fi

# SELF-FIRE GUARD (op#11269/11271, operator-caught twice): a singleton can only
# PREP its own recycle (write a handoff, clean its inbox) — it can NEVER fire its
# own /clear. If this runs from INSIDE the target '$SESS' session (Nazim invoking
# it in his own live turn via the Bash tool), the send-keys below interleave with
# that live turn: the /clear stages into the composer but never commits, while the
# reconstitution/boot injection still lands — the "cleared but didn't reconstitute"
# boot-before-clear half-state the operator saw. A PEER body must fire the actual
# /clear: the operator's ↺ reset button, or the hub via
#   ssh Musa@sheikhs-mac-mini bash ~/wingmen/orchestrator/scripts/reset_nazim.sh
# Detection: a process launched inside a tmux pane inherits $TMUX_PANE; resolve the
# session that pane belongs to and refuse if it IS the target. External callers
# (reset button, ssh, a non-tmux shell, or another session's pane) all pass.
if [ -n "${TMUX_PANE:-}" ]; then
  _caller_sess="$("$TM" display-message -p -t "${TMUX_PANE}" '#S' 2>/dev/null || echo)"
  if [ "$_caller_sess" = "$SESS" ]; then
    echo "[reset_nazim] SELF-FIRE REFUSED: invoked from INSIDE the '$SESS' session." >&2
    echo "  A singleton cannot /clear its own live turn — the /clear would stage un-executed" >&2
    echo "  (boot-before-clear). Fire it EXTERNALLY: the operator's reset button, or the hub via" >&2
    echo "    ssh Musa@sheikhs-mac-mini bash ~/wingmen/orchestrator/scripts/reset_nazim.sh" >&2
    if [ "${RESET_ALLOW_SELF:-0}" != 1 ]; then exit 5; fi
    echo "[reset_nazim] RESET_ALLOW_SELF=1 — proceeding despite self-fire (NOT for a real recycle)." >&2
  fi
fi

# BUSY GATE (op#18941 — the gap this reset had that reset_orch/reset_cai did not):
# a /clear discards whatever turn is in flight, so NEVER clear a BUSY body. The
# definition of "busy" lives in the shared lib (pane_busy) so this and the sibling
# resets cannot drift — and pane_busy now also catches EXTENDED-THINKING turns
# (c0818b6), the exact case op#18941 missed: the oracle caught console WORKING, a
# single transient-idle sample overrode it, and the reset raced a live turn. This
# gate structurally refuses a thinking/foreground-busy console. A busy marker that
# was PRESENT but FROZEN (byte-identical across the sample window) is treated as
# NOT busy and said so aloud — a live turn animates. RESET_FORCE=1 is the loud
# escape hatch for a genuinely-wedged body. Evaluated in DRYRUN too. Exit 5 mirrors
# the siblings' unsafe-body-state code.
pane_busy "$TM" "$PANE"
if [ "${CC_BUSY_STALE:-0}" = 1 ]; then
  echo "WARNING: '$SESS' showed a busy marker but the pane is FROZEN (byte-identical across the sample window)." >&2
  echo "         Treating it as NOT busy: a live wait animates. If work was in flight it is already lost, not lost by this reset." >&2
fi
if [ "$CC_BUSY" = 1 ]; then
  if [ "${RESET_FORCE:-0}" = "1" ]; then
    echo "WARNING: '$SESS' is BUSY — $CC_BUSY_REASON — RESET_FORCE=1 set, clearing ANYWAY." >&2
    echo "WARNING: in-flight work will be DISCARDED and is not recoverable." >&2
  else
    echo "[reset_nazim] BUSY GATE: FAIL — '$SESS' is BUSY — $CC_BUSY_REASON — refusing to clear." >&2
    echo "       Set RESET_FORCE=1 to override if the console is genuinely wedged." >&2
    exit 5
  fi
else
  echo "[reset_nazim] BUSY GATE: PASS — '$SESS' is not busy."
fi

# HANDOFF-FIRST GATE (op#10967): a recycle must never boot fresh-Nazim from a
# STALE board (or from nothing). Refuse the /clear unless a FRESH handoff exists
# — the target is expected to have WRITTEN a current handoff first. RESET_FORCE=1
# overrides (loud); RESET_DRYRUN=1 runs the gate + reports PASS/FAIL and exits
# WITHOUT clearing (for verifying the gate).
FRESH_MAX="${RESET_FRESH_MAX:-1800}"   # 30 min default
_now="$(date +%s)"
if [ -z "$HANDOFF" ]; then
  echo "[reset_nazim] HANDOFF-FIRST GATE: FAIL — no reports/nazim-handoff-*.md found (would boot from nothing). Write a handoff first." >&2
  if [ "${RESET_FORCE:-0}" != 1 ]; then echo "REFUSING /clear (RESET_FORCE=1 to override)." >&2; exit 3; fi
  echo "[reset_nazim] RESET_FORCE=1 — proceeding despite no handoff." >&2
else
  _mtime="$(stat -f %m "$HANDOFF" 2>/dev/null || stat -c %Y "$HANDOFF" 2>/dev/null || echo 0)"
  _age=$(( _now - _mtime ))
  if [ "$_age" -gt "$FRESH_MAX" ]; then
    echo "[reset_nazim] HANDOFF-FIRST GATE: FAIL — newest handoff $HANDOFF is ${_age}s old (> ${FRESH_MAX}s), STALE. Write a fresh handoff first." >&2
    if [ "${RESET_FORCE:-0}" != 1 ]; then echo "REFUSING /clear (RESET_FORCE=1 to override)." >&2; exit 4; fi
    echo "[reset_nazim] RESET_FORCE=1 — proceeding despite stale handoff (${_age}s)." >&2
  else
    echo "[reset_nazim] HANDOFF-FIRST GATE: PASS — $HANDOFF is fresh (${_age}s old, <= ${FRESH_MAX}s)."
  fi
fi
# LAYER 3 (op#11594) — QUEUED-COMPOSER GATE (pre-fire, alongside idle+empty): a
# QUEUED/dim composer message (typed+Enter while busy) is INERT to the BSpace wipe
# below (FIX-3) — the /clear would stage BEHIND it and never run. Refuse up front
# rather than fire into a jam. RESET_FORCE=1 overrides (loud). Evaluated in DRYRUN too.
if "$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep -q "Press up to edit queued messages"; then
  echo "[reset_nazim] QUEUED-COMPOSER GATE: FAIL — '$SESS' has a QUEUED/dim composer message (would jam the /clear, op#11594)." >&2
  echo "  Drain/clear the queue first (or have the target self-clear), THEN re-fire." >&2
  if [ "${RESET_FORCE:-0}" != 1 ]; then echo "REFUSING /clear (RESET_FORCE=1 to override)." >&2; exit 7; fi
  echo "[reset_nazim] RESET_FORCE=1 — proceeding despite queued composer." >&2
else
  echo "[reset_nazim] QUEUED-COMPOSER GATE: PASS — no queued/dim composer message."
fi

if [ "${RESET_DRYRUN:-0}" = 1 ]; then
  echo "[reset_nazim] RESET_DRYRUN=1 — gates evaluated, NOT clearing. Exiting."; exit 0
fi

# LAYER 1 (op#11594) — QUIESCE THE RACE: pause THIS body's bus-notify for the fire
# window. A nazim_bus_notify nudge that landed between '/clear' and its Enter queued
# AHEAD of the /clear, so /clear went dim (never ran) while the script said 'done'.
# Bootout the daemon for the fire window; RESTORE in a trap/finally so a mid-fire
# crash can NEVER dangle notify-off (the state that had to be cleaned by hand).
NOTIFY_LABEL="dev.wingmen.nazim-bus-notify"
_notify_paused=0
_restore_notify() {
  [ "$_notify_paused" = 1 ] || return 0
  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$NOTIFY_LABEL.plist" 2>/dev/null \
    || launchctl kickstart "gui/$(id -u)/$NOTIFY_LABEL" 2>/dev/null || true
  echo "[reset_nazim] restored $NOTIFY_LABEL"
}
trap '_restore_notify' EXIT
if launchctl bootout "gui/$(id -u)/$NOTIFY_LABEL" 2>/dev/null; then
  _notify_paused=1; echo "[reset_nazim] paused $NOTIFY_LABEL for the fire window"
else
  echo "[reset_nazim] note: could not pause $NOTIFY_LABEL (may already be stopped) — proceeding" >&2
fi

# CAPTURE THE COMPOSER BEFORE WIPING IT — an idle console can hold its OWN staged next
# step; preserve it verbatim rather than silently destroying it (parity with reset_cai +
# reset_fleet_health, per cc-fleet-health 18210; the old raw `-N 80 BSpace` clobbered it).
# NOTE: this CC_EMPTY read shares the fleet-wide ghost-vs-real exposure (a dim history
# autosuggestion can read as real staged text) — same as the siblings; central fix is the
# composer_capture ghost belt (round 2). It fails SAFE here: worst case a false STAGED_NOTE,
# or the verify-empty below declines to /clear (console keeps its context, operator re-fires).
LOGDIR="$HOME/wingmen/orchestrator/logs"
STAGED_NOTE=""
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ]; then
  mkdir -p "$LOGDIR"
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$CC_MULTILINE" = 1 ] || [ -n "$CC_MARKER" ]; then
    printf '%s reset_nazim staged-composer %s (%s lines):\n' "$TS" "$CC_MARKER" "$CC_N" >> "$LOGDIR/reset_nazim_preserved_input.log"
    printf '%s\n' "$CC_RAW" | while IFS= read -r _l; do
      printf '%s reset_nazim staged-composer   | %s\n' "$TS" "$_l" >> "$LOGDIR/reset_nazim_preserved_input.log"
    done
    echo "[reset_nazim] PRESERVED staged composer ($CC_N lines) $CC_MARKER"
    STAGED_NOTE=" NOTE: you had multi-line text staged UNSENT in your composer when I cleared you (logs/reset_nazim_preserved_input.log, $CC_N lines, joined with ' / '): ${CC_FLAT}. Judge whether it was your own next step or an unsubmitted instruction; read the log before acting."
  else
    printf '%s reset_nazim staged-composer: %s\n' "$TS" "$CC_FLAT" >> "$LOGDIR/reset_nazim_preserved_input.log"
    echo "[reset_nazim] PRESERVED staged composer text: $CC_FLAT"
    STAGED_NOTE=" NOTE: you had \"${CC_FLAT}\" staged UNSENT in your composer when I cleared you (captured verbatim to logs/reset_nazim_preserved_input.log). Judge whether it was your own next step or something typed and never submitted; if it reads like an instruction, treat it as NOT yet carried out."
  fi
elif [ "$CC_PARTIAL" = 'noprompt' ]; then
  mkdir -p "$LOGDIR"
  printf '%s reset_nazim staged-composer %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CC_MARKER" >> "$LOGDIR/reset_nazim_preserved_input.log"
  echo "[reset_nazim] WARNING: $CC_MARKER" >&2
  STAGED_NOTE=" NOTE: I could NOT read your composer before clearing you (no prompt row in the capture) — do NOT read this as 'nothing was there'."
fi

# CC_GHOST (op#18467): if the preserved text was auto-classified a history-ghost, MARK the
# note so fresh-Nazim never reads it as certain staged work (logged above regardless).
[ "${CC_GHOST:-0}" = 1 ] && STAGED_NOTE="${STAGED_NOTE} (fleet-health auto-classified this as a history-GHOST of a prior submit — most likely NOT real staged work; preserved to the log regardless. Verify it wasn't your real next step.)"

# WIPE, sized to what was staged; verify empty BEFORE typing /clear into it — a dirty
# composer would make the /clear stage BEHIND the residue and never run.
WIPE=$(( CC_BYTES + 80 )); [ "$WIPE" -lt 200 ] && WIPE=200; [ "$WIPE" -gt 20000 ] && WIPE=20000
echo "[reset_nazim] clearing composer (${WIPE} BSpace for ${CC_BYTES}B staged) + sending /clear ..."
"$TM" send-keys -t "$PANE" -N "$WIPE" BSpace
sleep 1
# CC_GHOST (op#18467): a re-appearing history-GHOST reads CC_EMPTY=0 but is empty
# underneath (the /clear replaces it; its text was preserved+logged pre-wipe) — proceed
# on a confirmed ghost. A REAL residue (not a ghost) still refuses (unknown -> refuse).
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ] && [ "${CC_GHOST:-0}" != 1 ]; then
  # RESET_FORCE bypass (op#18548, mirrors reset_fleet_health): an invisible-submit history-
  # autosuggestion ghost reads CC_EMPTY=0 + CC_GHOST=0 (b' can't history-match a ghost whose
  # submit isn't visible), so verify-empty FALSE-blocks a body that is EMPTY-underneath. The
  # residue was ALREADY preserved+logged above, and /clear replaces the dim autosuggestion
  # cleanly. So RESET_FORCE=1 proceeds — LOUD, opt-in, non-lossy. USE ONLY when verified
  # empty-underneath (type a char over it -> it replaces cleanly). Stage-1 durable fix =
  # probe-confirmed-empty bypass (no manual force).
  if [ "${RESET_FORCE:-0}" = 1 ]; then
    echo "[reset_nazim] RESET_FORCE=1 — proceeding PAST verify-empty despite residue ('${CC_FLAT}'): treating as an invisible-submit history-ghost (empty-underneath); text preserved to the log above. op#18548." >&2
  else
    echo "ERROR: composer NOT empty after wipe — refusing to send /clear into dirty input (residue: ${CC_FLAT}). NAZIM UNCHANGED. (RESET_FORCE=1 to override IFF verified empty-underneath.)" >&2
    exit 6
  fi
fi
[ "$CC_PARTIAL" != 'ok' ] && echo "WARNING: post-wipe capture was $CC_PARTIAL — treating composer as empty on weak evidence." >&2
"$TM" send-keys -t "$PANE" -l "/clear"
sleep 1
# show what's staged so a human can eyeball before it commits
echo "[reset_nazim] composer now shows (should be a clean /clear):"
"$TM" capture-pane -t "$PANE" -p | grep -n "❯" | tail -2
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 4

# LAYER 2 (op#11594) — POST-FIRE DEAD-MAN VERIFY: confirm the /clear ACTUALLY ran.
# The jam left /clear DIM-QUEUED (never executed) yet the script said 'done' and
# injected the boot onto the STILL-BLOATED context — a false 'done' that burned
# operator trust. A real /clear leaves NO staged '❯ /clear' and DROPS context. Poll
# briefly; if it did NOT take, do NOT send the boot (that deepens the half-state) —
# FAIL LOUD and ROUTE the failure to the operator via an orch-console bus row
# (console relays), so a stuck recycle never LOOKS done.
_cleared=0
for _i in 1 2 3 4; do
  # Check the LIVE COMPOSER via composer_capture (the fleet's ONE dim-ghost-vs-real
  # definition), NOT whole-pane scrollback: a successful /clear EMPTIES the live
  # composer; a jammed/dim-queued /clear leaves '/clear' staged in it. The old
  # `grep "❯ /clear"` matched the persistent /clear ECHO in scrollback + the
  # `% context used` gauge (not rendered at low ctx in this CC version) — both
  # unreliable, so a REAL clear false-failed (cc-fleet-health 18169).
  # GATE ON CONFIDENCE: CC_EMPTY=1 alone is NOT enough — a 'noprompt' (unreadable)
  # capture ALSO reports CC_N=0 -> CC_EMPTY=1 (composer_capture.sh: "we do not know
  # it was empty, only that we could not read it"). Trusting that as 'cleared' would
  # re-open the false-'done' door on a transient unreadable pane, so require
  # CC_PARTIAL='ok'. Weak evidence keeps polling; all-4 unreadable -> FAIL LOUD.
  # FAST-PATH PASS: a confident-empty live composer = cleared. A real jam leaves
  # '/clear' staged in the composer (CC_EMPTY=0), so the fast path can NEVER pass a jam.
  composer_parse_pane "$TM" "$PANE"
  if [ "$CC_EMPTY" = 1 ] && [ "$CC_PARTIAL" = 'ok' ]; then _cleared=1; break; fi
  # GHOST-IMMUNE TRANSCRIPT BELT (cc-fleet-health, verified 2026-08-10; bus 18222).
  # CC_EMPTY=0 is ambiguous: a real jam ('/clear' staged) OR a dim history-autosuggestion
  # GHOST in an actually-empty composer — composer_capture can't tell them apart
  # statically ([[composer-ghost-false-positive]]), so keying FAIL on CC_EMPTY alone
  # re-opens a false-fail door. The TRANSCRIPT disambiguates: a real /clear repaints the
  # fresh-session banner (the 'Claude Code v<N>' logo line) on the VISIBLE screen, which a
  # composer-box ghost can NEVER fake; a jam leaves the old conversation tail (no logo).
  # VERIFIED (throwaway /clear): the visible pane repaints ' ▐▛███▜▌   Claude Code v2.1.226'.
  # Grep the VISIBLE pane (`capture-pane -p`, current screen) NOT scrollback — a long
  # pre-clear session may have an OLD logo in history. Sound for the reset use-case:
  # resets fire on BLOATED bodies whose original banner has long scrolled off, so a
  # banner on the VISIBLE screen means a real repaint, not stale history.
  if "$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep -qE 'Claude Code v[0-9]'; then
    _cleared=1; break
  fi
  sleep 2
done
if [ "$_cleared" != 1 ]; then
  echo "[reset_nazim] LAYER-2 VERIFY: FAIL — /clear did NOT execute (unread/staged/high-context). NOT sending boot." >&2
  echo "[reset_nazim] live composer at fail: partial=${CC_PARTIAL:-?} empty=${CC_EMPTY:-?} staged=${CC_FLAT:-<none>}" >&2
  # message_type='blocker' (NOT 'alert' — 'alert' violates agent_messages_message_type_check;
  # cc-quality #17933). The python EXIT CODE gates the 'escalated' claim below: exit 0 only
  # after the row commits; exit 1 if DSN unset OR the write raises — so we never CLAIM an
  # operator escalation that didn't land (the very false-'done' Layer 2 exists to kill).
  _escalated=0
  if [ -x "$HOME/wingmen/orchestrator/.venv/bin/python3" ]; then
    if "$HOME/wingmen/orchestrator/.venv/bin/python3" - "$SESS" 2>/dev/null <<'PYESC'
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
sess = sys.argv[1] if len(sys.argv) > 1 else "nazim"
if not dsn:
    sys.exit(1)  # could NOT escalate
body = (f"LOUD: reset_{sess} FIRED but the /clear did NOT execute (dim-queued/jam, op#11594) — the "
        f"recycle is STUCK, NOT done. The body is still on its bloated context and NO boot instruction "
        f"was injected. OPERATOR: the last recycle did not take — please re-fire on a clean composer. "
        f"(Auto-escalated by the hardened reset; L1 pause + L3 gate should prevent this, so a recurrence "
        f"warrants investigation.)")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response) "
        "VALUES ('cc-fleet-health','orch-console','blocker',%s,%s,'P1',true)",
        (f"LOUD: reset_{sess} STUCK — /clear did not take, recycle NOT done (relay to operator)", body))
    conn.commit()
sys.exit(0)  # escalated OK
PYESC
    then _escalated=1; fi
  fi
  if [ "$_escalated" = 1 ]; then
    echo "[reset_nazim] escalated LOUD failure to orch-console (operator relay). Exiting non-zero." >&2
  else
    echo "[reset_nazim] WARNING: could NOT write the operator-relay row (DB unset/unreachable). The recycle STILL FAILED — escalate to the operator MANUALLY." >&2
  fi
  exit 8
fi
echo "[reset_nazim] LAYER-2 VERIFY: PASS — /clear executed (context cleared, no staged /clear)."

# DOCTRINE-ONLY SCAFFOLD (see the 2026-08-01 slim). Everything below is durable,
# situation-AGNOSTIC behavioural doctrine — it names NO specific live item, lane,
# or thread. All situational state (the current LIVE ITEM, lane roster + status,
# pending operator decisions) lives in the newest handoff, which this string tells
# fresh-Nazim to read IN FULL. Rationale: the operator (op#8881) caught that inline
# "live" specifics baked into this string go stale the moment the situation moves,
# and a reset would then inject them as if current. Keep this string free of
# specifics; put live state in the handoff. If you find yourself wanting to add a
# named thread here, add it to the handoff instead.
BOOT="You are Nazim (orch-console), the operator's CTO console on the Mac Mini, freshly reset in-place (operator-requested). Confirm your model at the start. FIRST read ${HANDOFF} IN FULL — its ⚑ FINAL STATE block first, which supersedes anything above it — then CLAUDE.md. That handoff carries ALL live state (open threads, the current LIVE ITEM, lane roster + status, pending operator decisions). This boot string is a fixed doctrine-only scaffold and deliberately names NO specifics, so it can never inject a stale 'live' claim — trust the handoff for what is actually happening now. Reconcile BOTH inboxes: operator_log.unprocessed() AND agent_messages to_agent='orch-console'; answer the operator ONLY via scripts/nazim_send.sh (NEVER the hub's tg_send) and stamp handled. cc-irsyad does NOT draft replies the hub is answering; before sending on the hub's client thread, re-read the last outbound row on that tag. Writing to the operator on another body's topic is a PROPOSAL THAT WAITS. Verify-not-assert EVERY 'done'; a name is not an implementation; a measurement whose tooling failed reports 'could not measure', never a finding. Then drive the board and tell the operator you are up.${STAGED_NOTE}"
echo "[reset_nazim] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"
sleep 1
"$TM" send-keys -t "$PANE" Enter
echo "[reset_nazim] done — fresh Nazim booting from $HANDOFF"
