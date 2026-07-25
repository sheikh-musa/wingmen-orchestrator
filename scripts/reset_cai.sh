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

echo "[reset_cai] clearing composer + sending /clear ..."
"$TM" send-keys -t "$PANE" -N 120 BSpace   # wipe any staged composer text
sleep 1
"$TM" send-keys -t "$PANE" -l "/clear"
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 4

BOOT="You are cai, the fleet's strategic node (agent_id='cai' exactly, singleton — never a sub-tag), freshly reset in-place at your own request (CAI-575, you were ~92% and P0-only). You are on claude-opus-5. FIRST read reports/cai-handoff-NOW.md IN FULL — your own restore point (ref 575, digest 866) — then CLAUDE.md. Reconcile agent_messages where to_agent='cai' and read_at is null; stamp what you process. THREE ITEMS HANDED FORWARD, unfinished: (1) v4 of the client correction to Gazzabyte — v1/v2/v3 each had defects the hub found, so re-derive from audit_chain_boundaries row 1 in the substrate rather than from any prior draft; (2) the ENTITY decision you own — grants + payment-corridor contracting + client-data custody + client-exit are ONE question, assembled once, and nothing gets registered in isolation; (3) the org/client-exit ruling. The Xendit memo is DONE (CAI-565, shipped early) — do not redo it. OPERATOR PERCEPTION, read before you message him: fleet volume to him is DOWN 55% this week, but you went from 0 to 15 direct messages in one day and sent corrections that superseded each other; he called it flip-flopping and he is right. Verify BEFORE the first message rather than correcting after, and send him only what changes what he would DO — the rest is a bus row to Nazim. Verify-not-assert every claim; a name is not an implementation; a measurement whose tooling failed must report 'could not measure', never a finding. Reply to Nazim (agent_messages to 'orch-console') once you are up."
echo "[reset_cai] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 3
echo "[reset_cai] done — fresh cai booting from $HANDOFF. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -6
