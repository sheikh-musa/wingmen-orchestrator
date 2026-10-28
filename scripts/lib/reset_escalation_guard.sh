#!/usr/bin/env bash
# reset_escalation_guard.sh — SOURCE this from the reset family's LAYER-2 escalation block
# (reset_cai/nazim/fleet_health/auditor.sh) right before it posts the "LOUD: reset_X STUCK
# (relay to operator)" row (orch-console #80174).
#
# Why: that row is an operator page. It leaked from TESTS twice (#78935 on 10-27, #80158 on
# 10-28 14:11Z): a test fakes tmux to drive the STUCK branch, and a scratch clone with the live
# env file symlinked in hands bus_send the live DSN. A real reset can only reach STUCK with a real
# tmux and a real session, so any test marker means the post must not happen. The bus_send
# sandbox checks guard the DSN leg; this guards the caller, so a stale or mis-wired clone can't
# page anyone either.
#
# reset_escalation_test_context: returns 0 and sets RESET_ESC_TEST_REASON when this process is
# in a test context (WINGMEN_TEST_SANDBOX set, PYTEST_CURRENT_TEST set, or $TM / tmux is a
# script stub or missing rather than the real binary); returns 1 for a real run.
# reset_escalation_refuse <script-name> <reason>: the fail-CLOSED path. Posts NOTHING, says so on
# stderr, and appends one line to $LOGDIR (default $ORCH_DIR/logs)/reset_escalation_refused.log.
# Never exits; the caller keeps its own exit 8.

reset_escalation_test_context() {
  RESET_ESC_TEST_REASON=''
  if [ -n "${WINGMEN_TEST_SANDBOX:-}" ]; then
    RESET_ESC_TEST_REASON='WINGMEN_TEST_SANDBOX is set'
  elif [ -n "${PYTEST_CURRENT_TEST:-}" ]; then
    RESET_ESC_TEST_REASON='PYTEST_CURRENT_TEST is set'
  else
    _reg_tm="${TM:-$(command -v tmux 2>/dev/null)}"
    if [ -z "$_reg_tm" ] || [ ! -f "$_reg_tm" ]; then
      RESET_ESC_TEST_REASON="tmux binary not found (${_reg_tm:-<none>})"
    elif [ "$(head -c2 "$_reg_tm" 2>/dev/null)" = '#!' ]; then
      # the real tmux is a compiled binary; a stub is a script
      RESET_ESC_TEST_REASON="tmux is a script stub ($_reg_tm), not the real binary"
    fi
    unset _reg_tm
  fi
  [ -n "$RESET_ESC_TEST_REASON" ]
}

reset_escalation_refuse() {
  _reg_log="${LOGDIR:-${ORCH_DIR:-.}/logs}/reset_escalation_refused.log"
  echo "[$1] REFUSED to post the operator-relay row: $2. NOTHING was posted (logged to $_reg_log)." >&2
  { mkdir -p "$(dirname "$_reg_log")" &&
    printf '%s %s escalation REFUSED (test context): %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >> "$_reg_log"
  } 2>/dev/null || echo "[$1] WARNING: the refusal log write ALSO failed ($_reg_log)." >&2
  unset _reg_log
  return 0
}
