#!/usr/bin/env bash
# reset_fleet_health.sh — in-place /clear + reboot-from-handoff of the cc-fleet-health
# (SRE) session. Mirrors the HARDENED reset_cai.sh / reset_nazim.sh (eb19002): in-place
# /clear (NOT kill) frees the context window; the boot reloads the SRE from its own
# restore point. Built 2026-08-11 to close the 2nd 'who watches the watchmen' gap the
# operator caught — the SRE recycles every OTHER body on bloat but had NO recycle path
# for itself, so at 82% degraded it could only idle (op#-flagged: "why idle when degraded?").
#
# WHY not kill: the SRE is a singleton whose relaunch path is operator-owned
# (boot_fleet_health.sh under tmux; fleet_lanes-unmanaged). A kill risks a second body /
# a dropped fleet_health_lease; clearing in place keeps the identity + the lease's
# background heartbeat loop (a separate subshell) alive.
#
# DIFFERENCES from reset_cai.sh: (1) NO Layer-1 bus-notify pause — the SRE has no
# per-body notify daemon to race (confirmed cc-fleet-health #18132), so there is nothing
# to quiesce; (2) tmux socket = /usr/local/bin/tmux (the fleet-health session lives on the
# Mini's /usr/local socket — the two-tmux-binaries trap). Everything else — self-fire
# guard, handoff gate, busy gate, L3 queued-composer gate, DRYRUN, composer-preserve,
# sized wipe + verify-empty, L2 post-clear dead-man verify + operator escalate — is the
# same hardened shape, reviewed by cc-fleet-health (its reset domain, CAI-779 symmetry).
#
# MUST run EXTERNALLY (not from inside the 'fleet-health' session — self-fire guard).
set -uo pipefail
FH_DIR="$HOME/wingmen/fleet-health"
HANDOFF="$FH_DIR/reports/fleet-health-handoff-NOW.md"
SESS="fleet-health"
PANE="${SESS}:0.0"
export PATH="/usr/local/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"
# The fleet-health session is on the Mini's /usr/local/bin/tmux socket (two-tmux trap).
TM="${TM:-$([ -x /usr/local/bin/tmux ] && echo /usr/local/bin/tmux || command -v tmux)}"

_RESET_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/composer_capture.sh
. "$_RESET_LIB_DIR/composer_capture.sh" || { echo "ERROR: composer_capture.sh missing" >&2; exit 9; }

"$TM" has-session -t "=$SESS" 2>/dev/null || { echo "ERROR: tmux session '$SESS' not found on this host/socket ($TM)." >&2; exit 1; }

# SELF-FIRE GUARD (CAI-779 Tier-B, mirrors reset_cai/reset_nazim): a body may PREP its
# own recycle but must NEVER fire its own /clear (the send-keys interleave with the
# caller's live turn → boot-before-clear half-state). Refuse if invoked from INSIDE the
# target session; fail-open so a resolver hiccup never blocks a real external reset.
if [ -n "${TMUX_PANE:-}" ]; then
  _caller_sess="$("$TM" display-message -p -t "${TMUX_PANE}" '#S' 2>/dev/null || echo)"
  if [ "$_caller_sess" = "$SESS" ]; then
    echo "[reset_fleet_health] SELF-FIRE REFUSED: invoked from INSIDE '$SESS' — a body cannot /clear its own live turn. Fire EXTERNALLY (orch-console/operator/ssh)." >&2
    if [ "${RESET_ALLOW_SELF:-0}" != 1 ]; then exit 5; fi
    echo "[reset_fleet_health] RESET_ALLOW_SELF=1 — proceeding despite self-fire (NOT for a real recycle)." >&2
  fi
fi
[ -f "$HANDOFF" ] || { echo "ERROR: restore point $HANDOFF missing — refusing to clear." >&2; exit 3; }

# Never clear a BUSY body: a /clear discards work in flight. 'busy' lives in the shared
# lib (pane_busy) so this and the sibling resets cannot drift. RESET_FORCE=1 overrides loud.
pane_busy "$TM" "$PANE"
if [ "${CC_BUSY_STALE:-0}" = 1 ]; then
  echo "WARNING: fleet-health showed a background-agent marker but the pane is FROZEN (byte-identical across the sample)." >&2
  echo "         Treating as NOT busy: a live wait animates. In-flight work, if any, is already lost — not by this reset." >&2
fi
if [ "$CC_BUSY" = 1 ]; then
  if [ "${RESET_FORCE:-0}" = "1" ]; then
    echo "WARNING: fleet-health is BUSY — $CC_BUSY_REASON — RESET_FORCE=1, clearing ANYWAY; in-flight work DISCARDED." >&2
  else
    echo "ERROR: fleet-health is BUSY — $CC_BUSY_REASON — refusing to clear. Set RESET_FORCE=1 if genuinely wedged." >&2
    exit 5
  fi
fi

# LAYER 3 — QUEUED-COMPOSER GATE: refuse if a QUEUED/dim message is present (inert to the
# BSpace wipe; the /clear would stage BEHIND it and never run). RESET_FORCE=1 overrides loud.
if "$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep -q "Press up to edit queued messages"; then
  echo "[reset_fleet_health] QUEUED-COMPOSER GATE: FAIL — '$SESS' has a QUEUED/dim composer message (would jam the /clear). Drain it, then re-fire." >&2
  if [ "${RESET_FORCE:-0}" != 1 ]; then echo "REFUSING /clear (RESET_FORCE=1 to override)." >&2; exit 7; fi
  echo "[reset_fleet_health] RESET_FORCE=1 — proceeding despite queued composer." >&2
else
  echo "[reset_fleet_health] QUEUED-COMPOSER GATE: PASS — no queued/dim composer message."
fi

# RESET_DRYRUN — evaluate gates and exit WITHOUT clearing, so readiness can be verified.
if [ "${RESET_DRYRUN:-0}" = 1 ]; then
  echo "[reset_fleet_health] RESET_DRYRUN=1 — gates evaluated (has-session/self-fire/handoff/busy/queued), NOT clearing. Exiting."; exit 0
fi

# CAPTURE THE COMPOSER BEFORE WIPING IT — an idle body's composer can hold its OWN staged
# next step; preserve it verbatim rather than silently destroying it.
LOGDIR="$HOME/wingmen/orchestrator/logs"
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ]; then
  mkdir -p "$LOGDIR"
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$CC_MULTILINE" = 1 ] || [ -n "$CC_MARKER" ]; then
    printf '%s fleet-health-reset staged-composer %s (%s lines):\n' "$TS" "$CC_MARKER" "$CC_N" >> "$LOGDIR/reset_fleet_health_preserved_input.log"
    printf '%s\n' "$CC_RAW" | while IFS= read -r _l; do
      printf '%s fleet-health-reset staged-composer   | %s\n' "$TS" "$_l" >> "$LOGDIR/reset_fleet_health_preserved_input.log"
    done
    echo "[reset_fleet_health] PRESERVED staged composer ($CC_N lines) $CC_MARKER"
    STAGED_NOTE="NOTE: you had multi-line text staged UNSENT in your composer when I cleared you (logs/reset_fleet_health_preserved_input.log, $CC_N lines, joined with ' / '): ${CC_FLAT}. Treat as possibly incomplete + judge whether it was your own next step or an unsubmitted instruction; read the log before acting."
  else
    printf '%s fleet-health-reset staged-composer: %s\n' "$TS" "$CC_FLAT" >> "$LOGDIR/reset_fleet_health_preserved_input.log"
    echo "[reset_fleet_health] PRESERVED staged composer text: $CC_FLAT"
    STAGED_NOTE="NOTE: you had \"${CC_FLAT}\" staged UNSENT in your composer when I cleared you. Captured verbatim first. Judge whether it was your own next step or something typed and never submitted; if it reads like an instruction, treat it as NOT yet carried out."
  fi
elif [ "$CC_PARTIAL" = 'noprompt' ]; then
  mkdir -p "$LOGDIR"
  printf '%s fleet-health-reset staged-composer %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CC_MARKER" >> "$LOGDIR/reset_fleet_health_preserved_input.log"
  echo "[reset_fleet_health] WARNING: $CC_MARKER" >&2
  STAGED_NOTE="NOTE: I could NOT read your composer before clearing you (no prompt row in the capture) — do NOT read this as 'nothing was there'."
else
  STAGED_NOTE="NOTE: your composer was EMPTY when I cleared you — nothing staged, nothing lost."
fi

# CC_GHOST (op#18467): if the preserved text was auto-classified a history-ghost, MARK the
# note so the fresh SRE never reads it as certain staged work (logged above regardless).
[ "${CC_GHOST:-0}" = 1 ] && STAGED_NOTE="${STAGED_NOTE} (fleet-health auto-classified this as a history-GHOST of a prior submit — most likely NOT real staged work; preserved to the log regardless. Verify it wasn't your real next step.)"

# WIPE, sized to what was staged; verify empty BEFORE typing /clear into it.
WIPE=$(( CC_BYTES + 80 )); [ "$WIPE" -lt 200 ] && WIPE=200; [ "$WIPE" -gt 20000 ] && WIPE=20000
CC_BEFORE_WIPE="$CC_FLAT"   # the wipe below is also the ghost probe — keep its input
echo "[reset_fleet_health] clearing composer (${WIPE} BSpace for ${CC_BYTES}B staged) + sending /clear ..."

# FIRE-WINDOW HOLD (2026-08-16). Bootout-ing this body's bus-notify covers ONE of the
# keystroke sources on this host; the operator-ingest nudger, the wake subscriber, the
# wedge/SLA/context watchdogs, backlog_swipe and lane_nudge.sh can all type into the pane
# this script is midway through clearing, and a pause LIST only ever names the ones
# someone remembered. The hold is a lock every sender consults, so a sender written later
# stands off by default. Self-expiring, and released on EXIT — a crashed reset must never
# leave a body unreachable. See scripts/lib/fire_window.sh.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/fire_window.sh"
fire_window_hold "$SESS" 180 "reset_fleet_health fire window"
"$TM" send-keys -t "$PANE" -N "$WIPE" BSpace
sleep 1
# CC_GHOST (op#18467): a re-appearing history-GHOST reads CC_EMPTY=0 but is empty
# underneath (the /clear replaces it; its text was preserved+logged pre-wipe) — proceed
# on a confirmed ghost. A REAL residue (not a ghost) still refuses (unknown -> refuse).
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ] && [ "${CC_GHOST:-0}" != 1 ]; then
  # RESET_FORCE bypass (op#18548, SRE-authorized + orch-console 18490 fallback): a history-
  # autosuggestion GHOST whose ORIGINAL SUBMIT is NOT visible in the transcript reads
  # CC_EMPTY=0 + CC_GHOST=0 — (b') can't history-match an invisible-submit ghost — so verify-empty
  # FALSE-blocks a body that is EMPTY-underneath. The residue was ALREADY preserved+logged above,
  # and /clear replaces the dim autosuggestion cleanly (proven: typing a char over it replaces it).
  # So RESET_FORCE=1 proceeds — LOUD, opt-in, non-lossy (text logged first). USE ONLY when verified
  # empty-underneath. Stage-1 durable fix = a probe-confirmed-empty ghost bypass (no manual force).
  # PROBE-CONFIRMED-EMPTY BYPASS (orch-console 2026-08-16) — ported from reset_lane.sh
  # @37c6efb / reset_nazim.sh @1f3f2f2, where the rule is now 5-for-5 on wild ghosts. The wipe
  # just done IS the probe, so read its answer instead of discarding it: $WIPE backspaces cannot
  # leave real staged text byte-identical (ten characters of real input die in ten), so text that
  # survives UNCHANGED was never in the composer — it is the dim autosuggestion/history ghost an
  # idle pane paints into an EMPTY buffer. Until now that ghost could VETO this body's recycle,
  # which is how the operator was blocked from clearing his own console at 82% on 2026-08-15.
  #
  # The guard is NOT weakened. Residue that CHANGED but is still non-empty is a real PARTIAL wipe
  # — /clear would stage behind it and never run, the failure this check exists for — and still
  # refuses, now printing BOTH strings so the next reader can see which case they are in. Only the
  # byte-identical case is reclassified, and only after the text was preserved to the log above.
  if [ "$CC_FLAT" = "$CC_BEFORE_WIPE" ]; then
    echo "[reset_fleet_health] GHOST: composer byte-identical after ${WIPE} BSpace ('${CC_FLAT}') — real text cannot survive that, so it is EMPTY underneath. Proceeding (probe-confirmed, no force needed)." >&2
  elif [ "${RESET_FORCE:-0}" = 1 ]; then
    echo "[reset_fleet_health] RESET_FORCE=1 — proceeding PAST verify-empty despite residue ('${CC_FLAT}'): treating as an invisible-submit history-ghost (empty-underneath); text preserved to the log above. op#18548." >&2
  else
    echo "ERROR: composer NOT empty after wipe — refusing to send /clear into dirty input (residue: ${CC_FLAT}). SRE UNCHANGED. (RESET_FORCE=1 to override IFF verified empty-underneath.)" >&2
    exit 6
  fi
fi
[ "$CC_PARTIAL" != 'ok' ] && echo "WARNING: post-wipe capture was $CC_PARTIAL — treating composer as empty on weak evidence." >&2

"$TM" send-keys -t "$PANE" -l "/clear"; sleep 1
"$TM" send-keys -t "$PANE" Enter; sleep 4

# LAYER 2 — POST-FIRE DEAD-MAN VERIFY: confirm the /clear ACTUALLY ran before injecting the
# boot; if it was dim-queued the boot would land on the still-bloated context (the false
# 'done'). If it did NOT take, do NOT boot — FAIL LOUD + escalate to orch-console (relay).
_cleared=0
for _i in 1 2 3 4; do
  # Check the LIVE COMPOSER via composer_capture (the fleet's ONE dim-ghost-vs-real
  # definition), NOT the whole-pane scrollback: a successful /clear EMPTIES the live
  # composer, while a jammed/dim-queued /clear leaves '/clear' staged in it. The old
  # `grep "❯ /clear"` matched the persistent /clear ECHO in scrollback and the
  # `% context used` gauge (not rendered at low ctx in this CC version) — both
  # unreliable, so a REAL clear false-failed (cc-fleet-health 18169; it stranded the
  # reset_fleet_health recycle → boot had to be sent by hand).
  # CC_EMPTY=1 alone is NOT enough: an UNREADABLE ('noprompt') capture also yields
  # CC_N=0 -> CC_EMPTY=1 ("could not read", not "was empty"). Require CC_PARTIAL='ok'
  # so a transient read-miss after a JAM can't false-'cleared' -> boot onto bloated
  # context (cc-fleet-health review 18208, Finding A). Weak evidence keeps polling;
  # all-4-unreadable -> FAIL LOUD (the safe direction for a post-clear verify).
  composer_parse_pane "$TM" "$PANE"
  if [ "$CC_EMPTY" = 1 ] && [ "$CC_PARTIAL" = 'ok' ]; then _cleared=1; break; fi
  # GHOST-IMMUNE TRANSCRIPT BELT (cc-fleet-health, verified 18229): CC_EMPTY=0 is ambiguous
  # — a real jam ('/clear' staged) OR a dim history-autosuggestion GHOST in an actually-empty
  # composer (composer_capture can't tell them apart statically). A real /clear repaints the
  # fresh-session banner (the 'Claude Code v<N>' logo line) on the VISIBLE pane, which a
  # composer-box ghost can NEVER fake; a jam leaves the old conversation tail (no logo).
  # Matcher targets the logo line (v[0-9] load-bearing) — NOT the stale 'welcome to claude
  # code' string, which never renders on CC v2.1.226 and would false-FAIL forever.
  if "$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep -qE 'Claude Code v[0-9]'; then _cleared=1; break; fi
  sleep 2
done
if [ "$_cleared" != 1 ]; then
  echo "[reset_fleet_health] LAYER-2 VERIFY: FAIL — /clear did NOT execute (still staged/high-context). NOT sending boot." >&2
  _escalated=0
  if [ -x "$HOME/wingmen/orchestrator/.venv/bin/python3" ]; then
    if "$HOME/wingmen/orchestrator/.venv/bin/python3" - 2>/dev/null <<'PYESC'
import os, sys, psycopg
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if not dsn: sys.exit(1)
body = ("LOUD: reset_fleet_health FIRED but the /clear did NOT execute (dim-queued/jam) — the SRE recycle is "
        "STUCK, NOT done. The SRE is still on its bloated context and NO boot was injected. OPERATOR: please "
        "re-fire on a clean composer. (message_type='blocker' — 'alert' violates the check constraint.)")
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
    cur.execute("INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response) "
                "VALUES ('orch-console','orch-console','blocker',%s,%s,'P1',true)",
                ("LOUD: reset_fleet_health STUCK — /clear did not take, SRE recycle NOT done (relay to operator)", body))
    conn.commit()
sys.exit(0)
PYESC
    then _escalated=1; fi
  fi
  [ "$_escalated" = 1 ] && echo "[reset_fleet_health] escalated LOUD failure (operator relay). Exiting non-zero." >&2 \
                        || echo "[reset_fleet_health] WARNING: could NOT write escalation row — recycle STILL FAILED, escalate MANUALLY." >&2
  exit 8
fi
echo "[reset_fleet_health] LAYER-2 VERIFY: PASS — /clear executed (context cleared)."

BOOT="You are cc-fleet-health, the fleet SRE (fleet reliability / health), singleton (agent_id='cc-fleet-health' exactly, never a sub-tag), freshly reset in-place by ${RESET_BY:-orch-console/Nazim} at $(date -u +%Y-%m-%dT%H:%MZ) — a bloated body comes back fresh rather than sitting stuck. ⚠️ THIS BOOT MESSAGE MAKES NO CLAIM about your model OR your agenda — it is a fixed doctrine-only scaffold and it goes stale between resets (it used to hardcode a past reset's provenance and a live worklist, and would have handed you a previous reset's world as if it were today's). ${HANDOFF} IS THE AUTHORITY: read it IN FULL FIRST, then $FH_DIR/CLAUDE.md (your charter), and where they disagree the handoff wins — your handoff and your inbox, not this message, tell you what is actually live. TAKE/RENEW the fleet_health_lease AND verify the renewal loop (your dead-man's switch: last_hb advancing + lease renewed_at fresh) survived the reset — per your boot sequence. Reconcile agent_messages where to_agent='cc-fleet-health' and read_at is null; stamp what you process. ${STAGED_NOTE} STANDING, and these do not go stale: verify-not-assert (a name is not an implementation; a measurement whose tooling failed reports 'could not measure', never a finding); when a premise falls, RE-DERIVE. Reply to Nazim (agent_messages to 'orch-console') once you are up + confirm fresh ctx."
echo "[reset_fleet_health] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"; sleep 1
"$TM" send-keys -t "$PANE" Enter; sleep 3
echo "[reset_fleet_health] done — fresh cc-fleet-health booting from $HANDOFF. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -6
