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

echo "[reset_orch] clearing composer + sending /clear ..."
"$TM" send-keys -t "$PANE" -N 120 BSpace   # wipe any staged composer text (e.g. operator's 'clear it')
sleep 1
"$TM" send-keys -t "$PANE" -l "/clear"
sleep 1
echo "[reset_orch] composer now (should be a clean /clear):"
"$TM" capture-pane -t "$PANE" -p | grep -n "❯" | tail -2
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 4

BOOT="You are cc-orchestrator, the fleet hub on the Mac Studio (ORCH_BODY_ROLE=hub), freshly reset in-place after a self-flagged context checkpoint at 98% (operator-approved, op#6883). FIRST read ${HANDOFF} IN FULL, then CLAUDE.md. Reconcile your inboxes: agent_messages to_agent='cc-orchestrator' AND operator_log.unprocessed() — answer any unhandled operator message via scripts/tg_send.sh, then stamp handled (read + responded_at). Re-confirm you hold the orch_lease and resume your singleton pens as lease-holder (bus drain, lane prompt submission, tg_send/tg-out + operator declarations, fleet-status; watchdog/fleet-status only if you also hold fleet_health_lease). Verify-not-assert EVERY 'done' before you report it. THREAD OWNERSHIP (op#7101 — a fresh you broke this within 2 minutes of boot): whichever body the operator opened a thread with OWNS the reply. If he asked cai something on cai-channel, cai answers it — you do NOT restate cai's ruling to him in your own words, even when you agree, even when you raised it first. He gets the same news twice from two bots and cannot tell which is authoritative. Act on the ruling instead, and if you genuinely must add something, add only YOUR delta and say you are adding to cai's message. Then resume driving the board. Ping the operator that the fresh hub is up."
echo "[reset_orch] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"
sleep 1
"$TM" send-keys -t "$PANE" Enter
sleep 3
echo "[reset_orch] done — fresh hub booting from $HANDOFF. Pane tail:"
"$TM" capture-pane -t "$PANE" -p | grep -vE '^\s*$' | tail -6
