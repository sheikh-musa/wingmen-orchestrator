#!/usr/bin/env bash
# reset_orch.sh — in-place /clear + boot of the cc-orchestrator (hub) session.
# MUST run ON THE HUB HOST (the Studio), where tmux 'orch' lives. Nazim (Mini)
# invokes it over SSH: ssh Musa@mac-studio 'bash ~/wingmen/orchestrator/scripts/reset_orch.sh'
# It send-keys into tmux 'orch'; it does NOT /clear the shell that runs it.
# Why in-place /clear (not kill): boot relaunches with --continue, which would
# reload the bloated conversation — the /clear is what frees the context; the
# boot instruction reloads from session-handoff-NOW.md. Mirrors reset_nazim.sh.
# SAFETY: refuses unless ORCH_BODY_ROLE=hub (never /clear a console body as 'orch').
set -uo pipefail
cd "$HOME/wingmen/orchestrator" || { echo "ERROR: orch dir missing" >&2; exit 9; }
set -a; source .env; set +a
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:$PATH"

_RESET_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/composer_capture.sh
. "$_RESET_LIB_DIR/composer_capture.sh" || { echo "ERROR: composer_capture.sh missing" >&2; exit 9; }

TM="$(command -v tmux || echo /opt/homebrew/bin/tmux)"
SESS="${ORCH_TMUX_SESSION:-orch}"
PANE="${SESS}:0.0"
HANDOFF="reports/session-handoff-NOW.md"
ROLE="${ORCH_BODY_ROLE:-unset}"

if [ "$ROLE" != "hub" ]; then
  echo "ERROR: ORCH_BODY_ROLE=$ROLE (not hub) — refusing to /clear '$SESS' on a non-hub body." >&2
  exit 4
fi
if ! "$TM" has-session -t "$SESS" 2>/dev/null; then
  echo "ERROR: tmux session '$SESS' not found on this host." >&2
  exit 1
fi
[ -f "$HANDOFF" ] || { echo "ERROR: restore point $HANDOFF missing — refusing to clear." >&2; exit 3; }

# Never clear a BUSY body: a /clear discards work in flight.
# (reset_cai.sh has had a guard since it was written; the hub — the body with the
# most in-flight work to lose — did not. Ported 2026-07-26, same exit code 5.)
# The definition of "busy" lives in the shared lib because porting the OLD guard
# is exactly how it came to miss the background-agent case: see pane_busy().
# RESET_FORCE=1 is the escape hatch for a body that is genuinely wedged: it warns
# loudly and proceeds, so a forced clear is never silent in the log.
pane_busy "$TM" "$PANE"
# A busy marker that was PRESENT but frozen: say so rather than silently
# treating a stalled body as idle. Proceeding is correct (a frozen render
# means the state it asserts is long gone) but it must never be quiet.
if [ "${CC_BUSY_STALE:-0}" = 1 ]; then
  echo "WARNING: the hub showed a background-agent marker but the pane is FROZEN (byte-identical across the sample window)." >&2
  echo "         Treating it as NOT busy: a live wait animates. If work was in flight it is already lost, not lost by this reset." >&2
fi
if [ "$CC_BUSY" = 1 ]; then
  if [ "${RESET_FORCE:-0}" = "1" ]; then
    echo "WARNING: '$SESS' is BUSY — $CC_BUSY_REASON — RESET_FORCE=1 set, clearing ANYWAY." >&2
    echo "WARNING: in-flight work will be DISCARDED and is not recoverable." >&2
  else
    echo "ERROR: cc-orchestrator is BUSY — $CC_BUSY_REASON — refusing to clear." >&2
    echo "       Set RESET_FORCE=1 to override if the hub is genuinely wedged." >&2
    exit 5
  fi
fi

# CAPTURE THE COMPOSER BEFORE WIPING IT (2026-07-26, Nazim). The BSpace below
# destroys whatever is staged, and the boot text used to state a HARDCODED
# composer string from one particular past reset ("ok done, check again") — true
# once, then asserted to every future fresh hub as a fact about ITSELF. Tonight
# the pane actually held "now do giro", an operator instruction that had sat
# unsubmitted for ~37 minutes; booting with the stale sentence would have told
# the new body a false thing about its own history AND lost the real instruction.
# So: read it, log it durably, and hand the ACTUAL text to the fresh body.
# Same NBSP trap as nudge_cai.sh — the TUI renders the prompt as '❯' + U+00A0,
# so a pattern written with an ordinary space matches nothing.
# Multi-line: the old one-liner here took only the FIRST rendered line of the
# entry, so a wrapped or newline-containing instruction was logged as if it were
# the whole thing. scripts/lib/composer_capture.sh reads the whole composer box
# and, where it cannot be certain, says so instead of guessing.
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ]; then
  mkdir -p logs
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$CC_MULTILINE" = 1 ] || [ -n "$CC_MARKER" ]; then
    # Multi-line record: header + one timestamped '|' line per rendered line, so
    # the log stays grep-able AND verbatim. The marker is NOT decoration — the
    # capture cannot tell a hard newline from a soft wrap, so it is declared.
    printf '%s orch-reset staged-composer %s (%s lines):\n' "$TS" "$CC_MARKER" "$CC_N" \
      >> logs/reset_orch_preserved_input.log
    printf '%s\n' "$CC_RAW" | while IFS= read -r _l; do
      printf '%s orch-reset staged-composer   | %s\n' "$TS" "$_l" >> logs/reset_orch_preserved_input.log
    done
    echo "[reset_orch] PRESERVED staged composer ($CC_N lines) $CC_MARKER"
    printf '%s\n' "$CC_RAW" | sed 's/^/[reset_orch]   | /'
  else
    printf '%s orch-reset staged-composer: %s\n' "$TS" "$CC_FLAT" \
      >> logs/reset_orch_preserved_input.log
    echo "[reset_orch] PRESERVED staged composer text: $CC_FLAT"
  fi
  # CC_FLAT, never CC_RAW: a raw newline inside a send-keys -l payload submits
  # the boot message early and strands the rest as junk in the composer.
  STAGED_NOTE="NOTE: you had \"${CC_FLAT}\" staged UNSENT in your composer when I cleared you. I captured it verbatim first (logs/reset_orch_preserved_input.log) rather than letting it vanish. Judge for yourself whether it is your own next step or an instruction someone typed at your pane and never submitted — if it reads like an instruction, treat it as one that has NOT been carried out yet. It is yours to re-decide; I am not deciding it for you."
  if [ -n "$CC_MARKER" ]; then
    STAGED_NOTE="$STAGED_NOTE ${CC_MARKER} — it spanned ${CC_N} rendered lines and I joined them with ' / '; I cannot tell a hard newline from a soft wrap, so treat the above as possibly incomplete and read logs/reset_orch_preserved_input.log for the line-by-line record before acting on it."
  fi
elif [ "$CC_PARTIAL" = 'noprompt' ]; then
  # No ❯ prompt row in the capture at all. We must NOT reassure a fresh body that
  # nothing was staged — we do not know that. Say what we actually know.
  mkdir -p logs
  printf '%s orch-reset staged-composer %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CC_MARKER" \
    >> logs/reset_orch_preserved_input.log
  echo "[reset_orch] WARNING: $CC_MARKER" >&2
  STAGED_NOTE="NOTE: I could NOT read your composer before clearing you — the pane capture contained no prompt row, so I do not know whether anything was staged. Do NOT read this as 'nothing was there'. If an instruction of yours or the operator's seems to have gone missing around this reset, that is where it went."
else
  STAGED_NOTE="NOTE: your composer was EMPTY when I cleared you — nothing was staged and nothing was lost. (Stated so you do not go looking for something that was never there.)"
fi

# WIPE, sized to what was actually staged. The old fixed -N 120 under-wiped any
# entry longer than 120 chars: the residue survived, the literal '/clear' was
# appended to it, and the reset then typed the whole boot text into a dirty
# composer. CC_BYTES >= character count for UTF-8, plus headroom; over-wiping an
# empty composer is a verified no-op.
WIPE=$(( CC_BYTES + 80 ))
[ "$WIPE" -lt 200 ] && WIPE=200
[ "$WIPE" -gt 20000 ] && WIPE=20000
echo "[reset_orch] clearing composer (${WIPE} BSpace for ${CC_BYTES}B staged) + sending /clear ..."
"$TM" send-keys -t "$PANE" -N "$WIPE" BSpace
sleep 1

# VERIFY the composer is actually empty BEFORE sending /clear. Previously this
# was printed and nothing acted on it, so a failed wipe proceeded anyway.
composer_parse_pane "$TM" "$PANE"
if [ "$CC_EMPTY" != 1 ]; then
  echo "ERROR: composer NOT empty after wipe — refusing to send /clear into dirty input." >&2
  echo "       residue (${CC_N} lines): ${CC_FLAT}" >&2
  echo "       The hub is UNCHANGED and still holds its context. Clear the composer by hand" >&2
  echo "       (attach: tmux attach -t $SESS) and re-run. Staged text was already preserved above." >&2
  exit 6
fi
[ "$CC_PARTIAL" != 'ok' ] && echo "WARNING: post-wipe capture was $CC_PARTIAL — treating composer as empty on weak evidence." >&2

"$TM" send-keys -t "$PANE" -l "/clear"
sleep 1
echo "[reset_orch] composer now (should be a clean /clear):"
"$TM" capture-pane -t "$PANE" -p | grep -n "❯" | tail -2
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 4

BOOT="You are cc-orchestrator, the fleet hub (ORCH_BODY_ROLE=hub — confirm your host, model and lease from YOUR OWN .env / orch_lease, NOT from this string), freshly reset in-place (reset run by ${RESET_BY:-orch-console/Nazim} at $(date -u +%Y-%m-%dT%H:%MZ); a hardcoded op-ref AND a hardcoded host both used to sit here and went stale on every relocation — this scaffold now names no machine, op-ref or worklist on purpose, so it cannot inject a stale claim). FIRST read ${HANDOFF} IN FULL, then CLAUDE.md. Reconcile your inboxes: agent_messages to_agent='cc-orchestrator' AND operator_log.unprocessed() — answer any unhandled operator message via scripts/tg_send.sh, then stamp handled (read + responded_at). Re-confirm you hold the orch_lease and resume your singleton pens as lease-holder (bus drain, lane prompt submission, tg_send/tg-out + operator declarations, fleet-status; watchdog/fleet-status only if you also hold fleet_health_lease). Verify-not-assert EVERY 'done' before you report it. THREAD OWNERSHIP (op#7101 — a fresh you broke this within 2 minutes of boot): whichever body the operator opened a thread with OWNS the reply. If he asked cai something on cai-channel, cai answers it — you do NOT restate cai's ruling to him in your own words, even when you agree, even when you raised it first. He gets the same news twice from two bots and cannot tell which is authoritative. Act on the ruling instead, and if you genuinely must add something, add only YOUR delta and say you are adding to cai's message. Then resume driving the board. ${STAGED_NOTE} Ping the operator that the fresh hub is up."
echo "[reset_orch] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 3
echo "[reset_orch] done — fresh hub booting from $HANDOFF. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -6
