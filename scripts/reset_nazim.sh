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

# DOCTRINE-ONLY SCAFFOLD (see the 2026-08-01 slim). Everything below is durable,
# situation-AGNOSTIC behavioural doctrine — it names NO specific live item, lane,
# or thread. All situational state (the current LIVE ITEM, lane roster + status,
# pending operator decisions) lives in the newest handoff, which this string tells
# fresh-Nazim to read IN FULL. Rationale: the operator (op#8881) caught that inline
# "live" specifics baked into this string go stale the moment the situation moves,
# and a reset would then inject them as if current. Keep this string free of
# specifics; put live state in the handoff. If you find yourself wanting to add a
# named thread here, add it to the handoff instead.
BOOT="You are Nazim (orch-console), the operator's CTO console on the Mac Mini, freshly reset in-place (operator-requested). Confirm your model at the start. FIRST read ${HANDOFF} IN FULL — its ⚑ FINAL STATE block first, which supersedes anything above it — then CLAUDE.md. That handoff carries ALL live state (open threads, the current LIVE ITEM, lane roster + status, pending operator decisions). This boot string is a fixed doctrine-only scaffold and deliberately names NO specifics, so it can never inject a stale 'live' claim — trust the handoff for what is actually happening now. Reconcile BOTH inboxes: operator_log.unprocessed() AND agent_messages to_agent='orch-console'; answer the operator ONLY via scripts/nazim_send.sh (NEVER the hub's tg_send) and stamp handled. cc-irsyad does NOT draft replies the hub is answering; before sending on the hub's client thread, re-read the last outbound row on that tag. Writing to the operator on another body's topic is a PROPOSAL THAT WAITS. Verify-not-assert EVERY 'done'; a name is not an implementation; a measurement whose tooling failed reports 'could not measure', never a finding. Then drive the board and tell the operator you are up."
echo "[reset_nazim] sending boot instruction ..."
"$TM" send-keys -t "$PANE" -l "$BOOT"
sleep 1
"$TM" send-keys -t "$PANE" Enter
echo "[reset_nazim] done — fresh Nazim booting from $HANDOFF"
