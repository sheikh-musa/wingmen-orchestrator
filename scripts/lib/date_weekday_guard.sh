#!/usr/bin/env bash
# date_weekday_guard.sh — fail-closed guard against a weekday paired with the wrong date
# in an outbound operator/client message.
#
# WHY (2026-10-07, orch-console bus #57970): messages said "Thursday 9 October",
# "Mon 13 Oct", "Friday 17 October" — in 2026 those are Fri / Tue / Sat. One reached a
# client. Logic lives in scripts/lib/date_weekday_guard.py; this is the shell wrapper.
#
# Usage (source, then call with the exact outbound text):
#   source "$ORCH_DIR/scripts/lib/date_weekday_guard.sh"
#   _date_weekday_guard "$TEXT" || exit 6
#
# Returns 0 = clean; 6 = mismatch (REFUSED) OR the guard itself could not run.
# FAIL-CLOSED: a guard crash / missing python BLOCKS the send loudly — it must never
# silently wave a message through. A refusal is recognised only by a "REFUSED:" line;
# any other non-zero exit (missing python/script, import error, crash) is a crash.
# Invoked by FILE PATH (stdlib-only module), not `-m`, so the caller's cwd can never
# make a different checkout's copy of the module be the one that runs.
# Text goes via a pipe on stdin — never argv, and never a here-string (bash 3.2 backs
# those with a temp file on disk). bash 3.2 compatible.
_date_weekday_guard() {
  local text="$1"
  local orch="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
  local rc=0 err=""
  err="$(printf '%s' "$text" | "$orch/.venv/bin/python3" "$orch/scripts/lib/date_weekday_guard.py" 2>&1 >/dev/null)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  [ -n "$err" ] && printf '%s\n' "$err" >&2
  case "$err" in
    REFUSED:*)
      echo "ERROR: send BLOCKED by the weekday/date guard — nothing was sent." >&2 ;;
    *)
      echo "ERROR: FAIL-CLOSED — the weekday/date guard could not run (rc=$rc); NOT sending." >&2
      echo "Check $orch/.venv/bin/python3 and scripts/lib/date_weekday_guard.py, then retry." >&2 ;;
  esac
  return 6
}
