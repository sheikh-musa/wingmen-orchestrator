#!/usr/bin/env bash
# reset_auditor.sh — in-place /clear + reboot-from-handoff of an AUDITOR SINGLETON
# (cc-quality / cc-storefront / cc-finance). CAI-1392 C.
#
# Usage:  scripts/reset_auditor.sh <quality|storefront|finance>      (cc- prefix accepted)
#         RESET_DRYRUN=1 ...   evaluate every gate, clear nothing (no arm needed)
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
#   allowlist (2) -> has-session (1) -> self-fire (5) -> busy (5) -> fresh handoff (3)
#   -> queued composer (7) -> [RESET_DRYRUN stops here, exit 0] -> ARMED (4)
#   -> pre-clear audit row (10)
# then the reset_cai.sh fire sequence: fire-window hold, composer capture+preserve, sized
# wipe with the ghost rule, /clear, LAYER-2 dead-man verify (8, escalates LOUD), boot.
#
# DISARMED BY DEFAULT. First use needs cai's arm-sign (fleet-ops governance is cai's lane).
# RESET_AUDITOR_ARMED=1 arms one run; setting it before cai has signed is a boundary
# violation, not a shortcut. The arm-flip, once signed, is a reviewed commit of ARMED_DEFAULT.
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

ARMED_DEFAULT=0   # flipped to 1 ONLY by a reviewed commit after cai's arm-sign (CAI-1392 C)
REPORTS_DIR="${RESET_AUDITOR_REPORTS_DIR:-$ORCH_DIR/reports}"
MAX_AGE="${RESET_AUDITOR_HANDOFF_MAX_AGE:-900}"
LOGDIR="${RESET_AUDITOR_LOGDIR:-$ORCH_DIR/logs}"
RESET_BY="${RESET_BY:-cc-fleet-health}"
FORCED_GATES=""; BUSY_REASON=""   # recorded in the audit row (orch-console #49369)

# ── 1. ALLOWLIST ─────────────────────────────────────────────────────────────
case "${1:-}" in
  quality|cc-quality)       SHORT="quality" ;;
  storefront|cc-storefront) SHORT="storefront" ;;
  finance|cc-finance)       SHORT="finance" ;;
  *) echo "ERROR: reset_auditor.sh recycles ONLY the auditor singletons (quality|storefront|finance); got '${1:-}'. Workers use sre_lane_recycle.py; other singletons their own reset_<name>.sh." >&2; exit 2 ;;
esac
BASE="cc-$SHORT"; SESS="$SHORT"; PANE="${SESS}:0.0"

# ── 2. HAS-SESSION ───────────────────────────────────────────────────────────
"$TM" has-session -t "=$SESS" 2>/dev/null || { echo "ERROR: tmux session '$SESS' not found on this host." >&2; exit 1; }

# ── 3. SELF-FIRE GUARD (CAI-779 Tier-B, mirrors reset_cai.sh) ────────────────
# From INSIDE the target session the send-keys below interleave with the caller's own live
# turn (boot-before-clear half-state, op#11269/11271). Fail-open on a resolver hiccup.
if [ -n "${TMUX_PANE:-}" ]; then
  _caller_sess="$("$TM" display-message -p -t "${TMUX_PANE}" '#S' 2>/dev/null || echo)"
  if [ "$_caller_sess" = "$SESS" ]; then
    echo "[reset_auditor] SELF-FIRE REFUSED: invoked from INSIDE '$SESS' — a body cannot /clear its own live turn. Fire it EXTERNALLY." >&2
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

# ── 7. ARM GATE — disarmed by default until cai's arm-sign ───────────────────
# Comes AFTER the FORCE overrides above on purpose: RESET_FORCE only ever turns a gate's
# refusal into a recorded override — it can never reach the keystrokes of a disarmed run.
if [ "${RESET_AUDITOR_ARMED:-$ARMED_DEFAULT}" != 1 ]; then
  echo "[reset_auditor] DISARMED — reset_auditor.sh needs cai's arm-sign before first use (CAI-1392 C). Nothing cleared. Gates otherwise PASS; RESET_DRYRUN=1 shows them." >&2
  exit 4
fi

# ── 8. PRE-CLEAR AUDIT ROW — no row, no /clear (dead-man's switch) ───────────
_reason="${RESET_REASON:-auditor recycle (bloat/idle seam)}"
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
"$TM" send-keys -t "$PANE" -N "$WIPE" BSpace; sleep 1

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

"$TM" send-keys -t "$PANE" -l "/clear"; sleep 1
"$TM" send-keys -t "$PANE" Enter; sleep 4

# LAYER-2 DEAD-MAN VERIFY: confirm the /clear actually ran before booting; a boot onto a
# still-bloated context is a false 'done'. Live composer empty on a readable capture, or the
# fresh-session banner (which a composer ghost can never fake).
_cleared=0
for _i in 1 2 3 4; do
  composer_parse_pane "$TM" "$PANE"
  if [ "$CC_EMPTY" = 1 ] && [ "$CC_PARTIAL" = 'ok' ]; then _cleared=1; break; fi
  if "$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep -E 'Claude Code v[0-9]' >/dev/null; then _cleared=1; break; fi
  sleep 2
done
if [ "$_cleared" != 1 ]; then
  echo "[reset_auditor] LAYER-2 VERIFY: FAIL — /clear did NOT execute on $BASE. NOT sending boot." >&2
  if "$PY" - "$RESET_BY" "$BASE" <<'PYESC' 2>/dev/null
import sys
sys.path.insert(0, __import__("os").path.join(__import__("os").environ.get("ORCH_DIR", ""), "scripts", "lib"))
from substrate_dsn import dsn_from_env_file
import psycopg
by, base = sys.argv[1], sys.argv[2]
with psycopg.connect(dsn_from_env_file()) as c, c.cursor() as cur:
    cur.execute("INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response) "
                "VALUES (%s,'orch-console','blocker',%s,%s,'P1',true)",
                (by, f"LOUD: reset_auditor {base} STUCK — /clear did not take, recycle NOT done",
                 f"reset_auditor.sh fired on {base} but the /clear did not execute; NO boot was sent. "
                 f"The body is still on its old context. Re-fire on a clean composer."))
    c.commit()
PYESC
  then echo "[reset_auditor] escalated LOUD failure to orch-console." >&2
  else echo "[reset_auditor] WARNING: could NOT write the escalation row — escalate MANUALLY. The recycle FAILED." >&2
  fi
  exit 8
fi
echo "[reset_auditor] LAYER-2 VERIFY: PASS — /clear executed."

BOOT="You are $BASE, an auditor singleton (CAI-500), freshly reset IN-PLACE by ${RESET_BY} at $(date -u +%Y-%m-%dT%H:%MZ) via reset_auditor.sh — same process, so your token and model are unchanged. Read your handoff at ${HANDOFF} IN FULL FIRST (absolute path — not your cwd's reports/); it is the authority over this message. Then reconcile agent_messages where to_agent='${BASE}' (and your instance id) and read_at is null; re-claim any open review it lists before taking new work. DB: source ~/wingmen/orchestrator/.env (file-first) before any DB command — your process env may hold a pre-rotation DSN. ${STAGED_NOTE} STANDING: verify-not-assert; a measurement whose tooling failed reports 'could not measure', never a finding; when a premise falls, RE-DERIVE. Reply to orch-console once you are up."
echo "[reset_auditor] booting '$SESS' ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"; sleep 1
"$TM" send-keys -t "$PANE" Enter; sleep 3
echo "[reset_auditor] done — $BASE booting from $HANDOFF. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -5
