#!/usr/bin/env bash
# reset_nazim.sh — in-place /clear + boot of the Nazim (orch-console) session.
# Run this FROM ANOTHER shell — the operator on the Mini, or the hub via
#   ssh Musa@sheikhs-mac-mini bash ~/wingmen/orchestrator/scripts/reset_nazim.sh
# It send-keys into tmux 'nazim'; it does NOT /clear the shell that runs it.
# Why in-place /clear (not kill): boot_nazim relaunches with --continue, which
# would reload the bloated conversation — same trap as the hub. The /clear is
# what actually frees the context; the boot instruction reloads from the handoff.
set -uo pipefail

TM="$(command -v tmux || echo /opt/homebrew/bin/tmux)"
SESS="nazim"
PANE="${SESS}:0.0"
HANDOFF="reports/nazim-handoff-20260725-fable-irsyad.md"

if ! "$TM" has-session -t "$SESS" 2>/dev/null; then
  echo "ERROR: tmux session '$SESS' not found on this host. Are you on the Mini?" >&2
  exit 1
fi

echo "[reset_nazim] clearing composer + sending /clear ..."
"$TM" send-keys -t "$PANE" -N 80 BSpace   # clear any stray/real composer content (ghost placeholder is harmless)
sleep 1
"$TM" send-keys -t "$PANE" -l "/clear"
sleep 1
# show what's staged so a human can eyeball before it commits
echo "[reset_nazim] composer now shows (should be a clean /clear):"
"$TM" capture-pane -t "$PANE" -p | grep -n "❯" | tail -2
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 4

BOOT="You are Nazim (orch-console), the operator's CTO console on the Mac Mini, freshly reset in-place after a full-context checkpoint (operator-approved op#6976). Confirm your model at the start. FIRST read ${HANDOFF} IN FULL, then CLAUDE.md. YOUR PRIORITY (op#6966/6976): spearhead the irsyad direct-agent build — see the handoff's ★ section. Reconcile BOTH inboxes: operator_log.unprocessed() AND agent_messages to_agent='orch-console' — answer any unhandled operator message via scripts/nazim_send.sh (NEVER the hub's tg_send), stamp handled (read + responded_at). RE-ARM the dev-group monitor (you lost it in the reset — see handoff INTERIM MANNING) or poll the cosem channels; reply in-group via scripts/dev_group_send.sh. Verify-not-assert EVERY 'done'. Then drive irsyad-direct + the board. Ping the operator that fresh-Nazim is up + on which model."
echo "[reset_nazim] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"
sleep 1
"$TM" send-keys -t "$PANE" Enter
echo "[reset_nazim] done — fresh Nazim booting from $HANDOFF"
