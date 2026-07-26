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

"$TM" has-session -t "=$SESS" 2>/dev/null || { echo "ERROR: tmux session '$SESS' not found on this host." >&2; exit 1; }
[ -f "$HANDOFF" ] || { echo "ERROR: restore point $HANDOFF missing — refusing to clear." >&2; exit 3; }

# Never clear mid-task: a /clear during a running turn discards work in flight.
if "$TM" capture-pane -t "$PANE" -p | tail -4 | grep -q 'esc to interrupt'; then
  echo "ERROR: cai is mid-task (pane shows 'esc to interrupt') — refusing to clear." >&2
  exit 5
fi

# CAPTURE THE COMPOSER BEFORE WIPING IT (2026-07-26, Nazim — same fix as reset_orch.sh).
# The BSpace below destroys whatever is staged. An idle body's composer holds its
# OWN staged next step, and on 2026-07-26 the hub's held "now do giro" — an
# operator instruction unsubmitted for 37 minutes that this pattern would have
# silently destroyed. Capture it, log it, hand it to the fresh body.
# NBSP trap: the TUI renders the prompt as '❯' + U+00A0, so a pattern written with
# an ordinary space matches nothing (this cost us a guard that never fired).
STAGED="$("$TM" capture-pane -t "$PANE" -p 2>/dev/null | grep '^❯' | tail -1 \
          | sed 's/^❯//' | LC_ALL=C sed 's/\xc2\xa0/ /g' \
          | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
if [ -n "$STAGED" ] && [ "$STAGED" != "Press up to edit queued messages" ]; then
  mkdir -p logs
  printf '%s cai-reset staged-composer: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$STAGED" \
    >> logs/reset_cai_preserved_input.log
  echo "[reset_cai] PRESERVED staged composer text: $STAGED"
  STAGED_NOTE="NOTE: you had \"${STAGED}\" staged UNSENT in your composer when I cleared you. Captured verbatim first (logs/reset_cai_preserved_input.log) rather than letting it vanish. Judge whether it was your own next step or something typed at your pane and never submitted; if it reads like an instruction, treat it as NOT yet carried out."
else
  STAGED_NOTE="NOTE: your composer was EMPTY when I cleared you — nothing staged, nothing lost."
fi

echo "[reset_cai] clearing composer + sending /clear ..."
"$TM" send-keys -t "$PANE" -N 120 BSpace   # wipe any staged composer text
sleep 1
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
