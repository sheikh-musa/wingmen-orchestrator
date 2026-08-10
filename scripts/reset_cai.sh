#!/usr/bin/env bash
# reset_cai.sh — in-place /clear + reboot-from-handoff of the cai session.
# MUST run ON THE HOST WHERE tmux 'cai' LIVES — currently the Mac Mini (cai moved
# off the Studio; op#11594 f/u). Run locally on the Mini, or invoke over SSH from a
# peer, e.g.:  bash ~/wingmen/orchestrator/scripts/reset_cai.sh
# Mirrors reset_orch.sh / reset_nazim.sh: in-place /clear (NOT kill) frees the context
# window; the boot instruction reloads cai from its own restore point.
#
# WHY not kill: cai is a singleton strategic node whose relaunch path is operator-owned
# (boot_cai.sh, fleet_lanes desired_state='down'). A kill risks coming back as a second
# cc-orchestrator (the boot_cai identity bug) — clearing in place keeps the identity.
# SAFETY: refuses unless cai's own restore point exists and the pane is idle.
set -uo pipefail
CAI_DIR="$HOME/wingmen/wingmen-cai"
HANDOFF="$CAI_DIR/reports/cai-handoff-NOW.md"
SESS="cai"
PANE="${SESS}:0.0"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
TM="${TM:-$(command -v tmux || echo /opt/homebrew/bin/tmux)}"

_RESET_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/composer_capture.sh
. "$_RESET_LIB_DIR/composer_capture.sh" || { echo "ERROR: composer_capture.sh missing" >&2; exit 9; }

"$TM" has-session -t "=$SESS" 2>/dev/null || { echo "ERROR: tmux session '$SESS' not found on this host." >&2; exit 1; }

# SELF-FIRE GUARD (CAI-779 Tier-B, mirrors reset_nazim.sh d975d1a — cc-quality-verified).
# A body may PREP its own recycle but must NEVER fire its own /clear: from INSIDE the
# target '$SESS' session the send-keys below interleave with the caller's live turn and
# stage a boot-before-clear half-state (op#11269/11271). A process launched in a tmux
# pane inherits $TMUX_PANE; resolve its session and refuse if it IS the target. External
# callers (operator reset button, ssh, another session's pane, non-tmux shell) all pass
# — fail-open (`|| echo` -> empty != $SESS) so a resolver hiccup never blocks a real reset.
if [ -n "${TMUX_PANE:-}" ]; then
  _caller_sess="$("$TM" display-message -p -t "${TMUX_PANE}" '#S' 2>/dev/null || echo)"
  if [ "$_caller_sess" = "$SESS" ]; then
    echo "[reset_cai] SELF-FIRE REFUSED: invoked from INSIDE the '$SESS' session — a body cannot /clear its own live turn (boot-before-clear). Fire it EXTERNALLY (operator reset button, ssh, or another session's pane)." >&2
    if [ "${RESET_ALLOW_SELF:-0}" != 1 ]; then exit 5; fi
    echo "[reset_cai] RESET_ALLOW_SELF=1 — proceeding despite self-fire (NOT for a real recycle)." >&2
  fi
fi
[ -f "$HANDOFF" ] || { echo "ERROR: restore point $HANDOFF missing — refusing to clear." >&2; exit 3; }

# Never clear a BUSY body: a /clear discards work in flight. The definition of
# "busy" lives in the shared lib (pane_busy) so this script and reset_orch.sh
# cannot drift — drift is exactly what let a foreground-only guard miss a body
# blocked on BACKGROUND AGENTS, which is the state cai was in at 07:00Z with
# four agents running and 'reset me' staged in its composer.
# RESET_FORCE=1 is the escape hatch (kept symmetric with reset_orch.sh): it warns
# loudly and proceeds, so a forced clear is never silent.
pane_busy "$TM" "$PANE"
# A busy marker that was PRESENT but frozen: say so rather than silently
# treating a stalled body as idle. Proceeding is correct (a frozen render
# means the state it asserts is long gone) but it must never be quiet.
if [ "${CC_BUSY_STALE:-0}" = 1 ]; then
  echo "WARNING: cai showed a background-agent marker but the pane is FROZEN (byte-identical across the sample window)." >&2
  echo "         Treating it as NOT busy: a live wait animates. If work was in flight it is already lost, not lost by this reset." >&2
fi
if [ "$CC_BUSY" = 1 ]; then
  if [ "${RESET_FORCE:-0}" = "1" ]; then
    echo "WARNING: cai is BUSY — $CC_BUSY_REASON — RESET_FORCE=1 set, clearing ANYWAY." >&2
    echo "WARNING: in-flight work will be DISCARDED and is not recoverable." >&2
  else
    echo "ERROR: cai is BUSY — $CC_BUSY_REASON — refusing to clear." >&2
    echo "       Set RESET_FORCE=1 to override if cai is genuinely wedged." >&2
    exit 5
  fi
fi

# LAYER 3 (op#11594) — QUEUED-COMPOSER GATE: refuse if a QUEUED/dim message is
# present (inert to the BSpace wipe below — the /clear would stage BEHIND it and
# never run). The post-wipe empty-verify below is a backstop; refuse up front too.
# RESET_FORCE=1 overrides (loud).
if "$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep -q "Press up to edit queued messages"; then
  echo "[reset_cai] QUEUED-COMPOSER GATE: FAIL — '$SESS' has a QUEUED/dim composer message (would jam the /clear, op#11594). Drain it first, then re-fire." >&2
  if [ "${RESET_FORCE:-0}" != 1 ]; then echo "REFUSING /clear (RESET_FORCE=1 to override)." >&2; exit 7; fi
  echo "[reset_cai] RESET_FORCE=1 — proceeding despite queued composer." >&2
else
  echo "[reset_cai] QUEUED-COMPOSER GATE: PASS — no queued/dim composer message."
fi

# RESET_DRYRUN (op#11594 — reset_cai previously had NONE; it fired live even with
# RESET_DRYRUN=1). Evaluate the gates and exit WITHOUT clearing, mirroring
# reset_nazim.sh, so a caller can verify readiness without firing.
if [ "${RESET_DRYRUN:-0}" = 1 ]; then
  echo "[reset_cai] RESET_DRYRUN=1 — gates evaluated (has-session/self-fire/handoff/busy/queued), NOT clearing. Exiting."; exit 0
fi

# LAYER 1 (op#11594) — QUIESCE THE RACE: pause THIS body's bus-notify for the fire
# window; RESTORE in a trap/finally so a mid-fire crash can NEVER dangle notify-off.
# A cai_bus_notify nudge landing between '/clear' and its Enter would queue AHEAD of
# the /clear (the op#11594 jam that hit console). Proven clean on cai this cycle.
NOTIFY_LABEL="dev.wingmen.cai-bus-notify"
_notify_paused=0
_restore_notify() {
  [ "$_notify_paused" = 1 ] || return 0
  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$NOTIFY_LABEL.plist" 2>/dev/null \
    || launchctl kickstart "gui/$(id -u)/$NOTIFY_LABEL" 2>/dev/null || true
  echo "[reset_cai] restored $NOTIFY_LABEL"
}
trap '_restore_notify' EXIT
if launchctl bootout "gui/$(id -u)/$NOTIFY_LABEL" 2>/dev/null; then
  _notify_paused=1; echo "[reset_cai] paused $NOTIFY_LABEL for the fire window"
else
  echo "[reset_cai] note: could not pause $NOTIFY_LABEL (may already be stopped / cross-host) — proceeding" >&2
fi

# CAPTURE THE COMPOSER BEFORE WIPING IT (2026-07-26, Nazim — same fix as reset_orch.sh).
# The BSpace below destroys whatever is staged. An idle body's composer holds its
# OWN staged next step, and on 2026-07-26 the hub's held "now do giro" — an
# operator instruction unsubmitted for 37 minutes that this pattern would have
# silently destroyed. Capture it, log it, hand it to the fresh body.
# NBSP trap: the TUI renders the prompt as '❯' + U+00A0, so a pattern written with
# an ordinary space matches nothing (this cost us a guard that never fired).
# Multi-line: the old one-liner here took only the FIRST rendered line, so a
# wrapped or newline-containing instruction was logged as if it were the whole
# thing. scripts/lib/composer_capture.sh reads the whole composer box and, where
# it cannot be certain, says so instead of guessing.
LOGDIR="$HOME/wingmen/orchestrator/logs"
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ]; then
  mkdir -p "$LOGDIR"
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$CC_MULTILINE" = 1 ] || [ -n "$CC_MARKER" ]; then
    printf '%s cai-reset staged-composer %s (%s lines):\n' "$TS" "$CC_MARKER" "$CC_N" \
      >> "$LOGDIR/reset_cai_preserved_input.log"
    printf '%s\n' "$CC_RAW" | while IFS= read -r _l; do
      printf '%s cai-reset staged-composer   | %s\n' "$TS" "$_l" >> "$LOGDIR/reset_cai_preserved_input.log"
    done
    echo "[reset_cai] PRESERVED staged composer ($CC_N lines) $CC_MARKER"
    printf '%s\n' "$CC_RAW" | sed 's/^/[reset_cai]   | /'
  else
    printf '%s cai-reset staged-composer: %s\n' "$TS" "$CC_FLAT" \
      >> "$LOGDIR/reset_cai_preserved_input.log"
    echo "[reset_cai] PRESERVED staged composer text: $CC_FLAT"
  fi
  # CC_FLAT, never CC_RAW: a raw newline in a send-keys -l payload submits early.
  STAGED_NOTE="NOTE: you had \"${CC_FLAT}\" staged UNSENT in your composer when I cleared you. Captured verbatim first (logs/reset_cai_preserved_input.log) rather than letting it vanish. Judge whether it was your own next step or something typed at your pane and never submitted; if it reads like an instruction, treat it as NOT yet carried out."
  if [ -n "$CC_MARKER" ]; then
    STAGED_NOTE="$STAGED_NOTE ${CC_MARKER} — it spanned ${CC_N} rendered lines and I joined them with ' / '; I cannot tell a hard newline from a soft wrap, so treat the above as possibly incomplete and read logs/reset_cai_preserved_input.log for the line-by-line record before acting on it."
  fi
elif [ "$CC_PARTIAL" = 'noprompt' ]; then
  # No ❯ prompt row in the capture — we do NOT know that nothing was staged, so
  # we must not tell a fresh body that nothing was lost.
  mkdir -p "$LOGDIR"
  printf '%s cai-reset staged-composer %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CC_MARKER" \
    >> "$LOGDIR/reset_cai_preserved_input.log"
  echo "[reset_cai] WARNING: $CC_MARKER" >&2
  STAGED_NOTE="NOTE: I could NOT read your composer before clearing you — the pane capture contained no prompt row, so I do not know whether anything was staged. Do NOT read this as 'nothing was there'."
else
  STAGED_NOTE="NOTE: your composer was EMPTY when I cleared you — nothing staged, nothing lost."
fi

# WIPE, sized to what was actually staged (the old fixed -N 120 left residue on
# any entry >120 chars, and '/clear' was then appended to that residue).
WIPE=$(( CC_BYTES + 80 ))
[ "$WIPE" -lt 200 ] && WIPE=200
[ "$WIPE" -gt 20000 ] && WIPE=20000
echo "[reset_cai] clearing composer (${WIPE} BSpace for ${CC_BYTES}B staged) + sending /clear ..."
"$TM" send-keys -t "$PANE" -N "$WIPE" BSpace
sleep 1

# VERIFY empty BEFORE /clear — never type a command into a dirty composer.
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ]; then
  echo "ERROR: composer NOT empty after wipe — refusing to send /clear into dirty input." >&2
  echo "       residue (${CC_N} lines): ${CC_FLAT}" >&2
  echo "       cai is UNCHANGED and still holds its context. Clear the composer by hand" >&2
  echo "       (attach: tmux attach -t $SESS) and re-run. Staged text was already preserved above." >&2
  exit 6
fi
[ "$CC_PARTIAL" != 'ok' ] && echo "WARNING: post-wipe capture was $CC_PARTIAL — treating composer as empty on weak evidence." >&2

"$TM" send-keys -t "$PANE" -l "/clear"
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 4

# LAYER 2 (op#11594) — POST-FIRE DEAD-MAN VERIFY: confirm the /clear ACTUALLY ran
# before injecting the boot. If a nudge (or anything) left /clear dim-queued, the
# boot would land on the STILL-BLOATED context (the false 'done' that burned
# operator trust on console). A real /clear leaves NO staged '❯ /clear' and DROPS
# context. Poll briefly; if it did NOT take, do NOT boot — FAIL LOUD and ROUTE the
# failure to the operator via an orch-console bus row (relayed), so a stuck recycle
# never LOOKS done.
_cleared=0
for _i in 1 2 3 4; do
  _post="$("$TM" capture-pane -t "$PANE" -p 2>/dev/null)"
  if ! printf '%s\n' "$_post" | grep -q "❯ /clear" \
     && ! printf '%s\n' "$_post" | grep -qE "[0-9]{2,3}% context used"; then
    _cleared=1; break
  fi
  sleep 2
done
if [ "$_cleared" != 1 ]; then
  echo "[reset_cai] LAYER-2 VERIFY: FAIL — /clear did NOT execute (still staged/high-context). NOT sending boot." >&2
  printf '%s\n' "$_post" | grep -nE "❯|% context used" | tail -4 >&2
  # message_type='blocker' (NOT 'alert' — 'alert' violates agent_messages_message_type_check;
  # cc-quality #17933). The python EXIT CODE gates the 'escalated' claim below: exit 0 only
  # after the row commits; exit 1 if DSN unset OR the write raises — so we never CLAIM an
  # operator escalation that didn't land (the very false-'done' Layer 2 exists to kill).
  _escalated=0
  if [ -x "$HOME/wingmen/orchestrator/.venv/bin/python3" ]; then
    if "$HOME/wingmen/orchestrator/.venv/bin/python3" - "$SESS" 2>/dev/null <<'PYESC'
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
sess = sys.argv[1] if len(sys.argv) > 1 else "cai"
if not dsn:
    sys.exit(1)  # could NOT escalate
body = (f"LOUD: reset_{sess} FIRED but the /clear did NOT execute (dim-queued/jam, op#11594) — the "
        f"recycle is STUCK, NOT done. The body is still on its bloated context and NO boot instruction "
        f"was injected. OPERATOR: the last {sess} recycle did not take — please re-fire on a clean composer. "
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
    echo "[reset_cai] escalated LOUD failure to orch-console (operator relay). Exiting non-zero." >&2
  else
    echo "[reset_cai] WARNING: could NOT write the operator-relay row (DB unset/unreachable). The recycle STILL FAILED — escalate to the operator MANUALLY." >&2
  fi
  exit 8
fi
echo "[reset_cai] LAYER-2 VERIFY: PASS — /clear executed (context cleared, no staged /clear)."

BOOT="You are cai, the fleet's strategic node (agent_id='cai' exactly, singleton — never a sub-tag), freshly reset in-place by ${RESET_BY:-orch-console/Nazim} at $(date -u +%Y-%m-%dT%H:%MZ). ⚠️ THIS BOOT MESSAGE MAKES NO CLAIM ABOUT WHICH MODEL YOU ARE RUNNING — it used to assert one, and it was wrong: your process argv, this .env and your live model disagreed while all three were individually truthful. Only YOUR OWN harness knows; if it matters, read it there. THIS BOOT MESSAGE IS ALSO NOT AUTHORITATIVE ON YOUR AGENDA — it is a fixed string and it goes stale between resets (it used to hardcode one past reset's provenance and a three-item worklist, and would have handed you a previous reset's world as if it were today's). ${HANDOFF} IS THE AUTHORITY: read it IN FULL FIRST, then CLAUDE.md, and where the two disagree the handoff wins. Reconcile agent_messages where to_agent='cai' and read_at is null; stamp what you process — your inbox, not this message, tells you what is actually live. ${STAGED_NOTE} STANDING, and these do not go stale: verify-not-assert every claim; a name is not an implementation; a measurement whose tooling failed must report 'could not measure', never a finding; when a premise falls, RE-DERIVE the conclusion rather than assuming it falls or stands with it. ON MESSAGING THE OPERATOR: verify BEFORE the first message rather than correcting after, and send him only what changes what he would DO — the rest is a bus row to Nazim. Reply to Nazim (agent_messages to 'orch-console') once you are up."
echo "[reset_cai] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 3
echo "[reset_cai] done — fresh cai booting from $HANDOFF. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -6
