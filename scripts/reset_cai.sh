#!/usr/bin/env bash
# reset_cai.sh — in-place /clear + reboot-from-handoff of the cai session.
# MUST run ON THE HOST WHERE tmux 'cai' LIVES (the Studio). Nazim (Mini) invokes it over SSH:
#   ssh Musa@mac-studio 'bash ~/wingmen/orchestrator/scripts/reset_cai.sh'
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
TM="$(command -v tmux || echo /opt/homebrew/bin/tmux)"

_RESET_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/composer_capture.sh
. "$_RESET_LIB_DIR/composer_capture.sh" || { echo "ERROR: composer_capture.sh missing" >&2; exit 9; }

"$TM" has-session -t "=$SESS" 2>/dev/null || { echo "ERROR: tmux session '$SESS' not found on this host." >&2; exit 1; }
[ -f "$HANDOFF" ] || { echo "ERROR: restore point $HANDOFF missing — refusing to clear." >&2; exit 3; }

# Never clear a BUSY body: a /clear discards work in flight. The definition of
# "busy" lives in the shared lib (pane_busy) so this script and reset_orch.sh
# cannot drift — drift is exactly what let a foreground-only guard miss a body
# blocked on BACKGROUND AGENTS, which is the state cai was in at 07:00Z with
# four agents running and 'reset me' staged in its composer.
# RESET_FORCE=1 is the escape hatch (kept symmetric with reset_orch.sh): it warns
# loudly and proceeds, so a forced clear is never silent.
pane_busy "$TM" "$PANE"
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

BOOT="You are cai, the fleet's strategic node (agent_id='cai' exactly, singleton — never a sub-tag), freshly reset in-place by ${RESET_BY:-orch-console/Nazim} at $(date -u +%Y-%m-%dT%H:%MZ). You are on claude-opus-5. ⚠️ THIS BOOT MESSAGE IS NOT AUTHORITATIVE ON YOUR AGENDA — it is a fixed string and it goes stale between resets (it used to hardcode one past reset's provenance and a three-item worklist, and would have handed you a previous reset's world as if it were today's). ${HANDOFF} IS THE AUTHORITY: read it IN FULL FIRST, then CLAUDE.md, and where the two disagree the handoff wins. Reconcile agent_messages where to_agent='cai' and read_at is null; stamp what you process — your inbox, not this message, tells you what is actually live. ${STAGED_NOTE} STANDING, and these do not go stale: verify-not-assert every claim; a name is not an implementation; a measurement whose tooling failed must report 'could not measure', never a finding; when a premise falls, RE-DERIVE the conclusion rather than assuming it falls or stands with it. ON MESSAGING THE OPERATOR: verify BEFORE the first message rather than correcting after, and send him only what changes what he would DO — the rest is a bus row to Nazim. Reply to Nazim (agent_messages to 'orch-console') once you are up."
echo "[reset_cai] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 3
echo "[reset_cai] done — fresh cai booting from $HANDOFF. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -6
