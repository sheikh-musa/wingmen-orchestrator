#!/usr/bin/env bash
# hub_self_recovery.sh — gzb-LOCAL wedge-recovery timer script (op#42896 follow-on,
# CAI-RESP-1439, hub consent bus #43157, orch-console binding design bus #43161).
#
# Runs every ~2min via systemd (deploy/wingmen-hub-self-recovery.timer), on gzb ONLY,
# checking the HUB's OWN tmux pane ('orch') for the WEDGED-but-ALIVE gap PR #145 left
# behind (hub_reach.py's gzb remedy is now honestly "escalate to a human" — this is
# the local answer). OBSERVE-FIRST: HSR_MODE defaults to 'observe' (log-only, never
# nudges) per orch-console's #43161 — the deployed systemd unit only ever passes
# 'observe' today; graduating to 'act' is orch-console's call, made later by editing
# the unit's ExecStart, never a default this script silently flips itself.
#
# Every gate below is a HARD refuse, never a soft preference (see
# nervous_system/hub_self_recovery.py's module docstring for the full condition
# provenance):
#   - orch_lease.holder_host must resolve to THIS host (gzbai) — acts on itself only
#   - the kill switch (a flag FILE here + a DB settings row, BOTH must allow) is
#     checked fresh every single tick, never cached
#   - ANY menu/picker refuses — composed from composer_capture.sh's pane_is_menu
#     PLUS trust_prompt_present + resume_menu_present (the model picker / trust
#     prompt / resume picker are not guaranteed to all hit pane_is_menu's generic
#     nav-footer regex, per orch-console's explicit "extend the fixtures" instruction)
#   - mid-turn / mid-autocompact (pane_is_busy) refuses
# The nudge itself (once graduated to 'act') delegates ENTIRELY to lane_nudge.sh —
# this script never types into the pane directly; it only decides WHETHER to ask
# lane_nudge.sh to, and what fixed payload to hand it.
set -uo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/composer_capture.sh
. "$ORCH_DIR/scripts/lib/composer_capture.sh" || { echo "hub_self_recovery: composer_capture.sh missing" >&2; exit 2; }

SESSION="orch"
HSR_MODE="${HSR_MODE:-observe}"
PY="$ORCH_DIR/.venv/bin/python3"; [ -x "$PY" ] || PY=python3
TMUX_BIN="$(command -v tmux || echo /usr/local/bin/tmux)"

if ! "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  # No live session here: process-death recovery is orch_supervisor.sh's job, not
  # this script's (this script exists ONLY for the wedged-but-ALIVE case). Audit
  # #43169 finding 6: still record a liveness stamp — without it, this branch
  # (e.g. the systemd unit's User= doesn't own the hub's tmux) would exit silently
  # forever, indistinguishable from a healthy tick that simply never ran.
  echo "hub_self_recovery: no '$SESSION' session on this host — nothing to check" >&2
  "$PY" -m nervous_system.hub_self_recovery --no-session >&2 || true
  exit 0
fi

PANE_TXT="$("$TMUX_BIN" capture-pane -t "$SESSION" -p -e 2>/dev/null)"
[ -n "$PANE_TXT" ] || PANE_TXT="$("$TMUX_BIN" capture-pane -t "$SESSION" -p 2>/dev/null)"

MENU=0
pane_is_menu "$TMUX_BIN" "$SESSION"; _menu_rc=$?
# Audit #43169 finding 5: pane_is_menu returns 0=menu, 1=not-a-menu, 2=unreadable.
# Fail CLOSED — only the explicit, positively-read "1" (not-a-menu) leaves MENU=0;
# an unreadable pane (2) must be treated as a menu, same as a genuine one (0),
# never silently treated as safe-to-act.
[ "$_menu_rc" = 1 ] || MENU=1
# Belt-and-suspenders (orch-console #43161(b)): these two are the fleet's OWN tested
# predicates for the picker screens pane_is_menu's generic nav-footer regex may not
# all hit (composer_capture.sh, already used by switch_lane_token's health-verify).
trust_prompt_present "$PANE_TXT" && MENU=1
resume_menu_present "$PANE_TXT" && MENU=1

BUSY=0
pane_is_busy "$TMUX_BIN" "$SESSION" && BUSY=1

composer_parse_pane "$TMUX_BIN" "$SESSION"
COMPOSER_EMPTY="${CC_EMPTY:-1}"

RESULT="$("$PY" -m nervous_system.hub_self_recovery --busy "$BUSY" --menu "$MENU" \
  --composer-empty "$COMPOSER_EMPTY" --mode "$HSR_MODE" 2>&1)"
RC=$?
echo "hub_self_recovery: $RESULT"
if [ "$RC" != 0 ]; then
  exit "$RC"
fi

ACT="$(printf '%s' "$RESULT" | "$PY" -c 'import json,sys
try:
    print(json.load(sys.stdin).get("act", False))
except Exception:
    print("False")' 2>/dev/null)"

if [ "$ACT" = "True" ]; then
  PAYLOAD="$(printf '%s' "$RESULT" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["payload"])')"
  echo "hub_self_recovery: ACT — delegating to lane_nudge.sh (fixed payload only, never templated)" >&2
  "$ORCH_DIR/scripts/lane_nudge.sh" "$SESSION" "$PAYLOAD"
  exit $?
fi
exit 0
