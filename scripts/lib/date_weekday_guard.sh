#!/usr/bin/env bash
# date_weekday_guard.sh — refuse an outbound operator/client message that pairs a weekday
# with the wrong date.
#
# WHY (2026-10-07, orch-console bus #57970): messages said "Thursday 9 October",
# "Mon 13 Oct", "Friday 17 October" — in 2026 those are Fri / Tue / Sat. One reached a
# client. Logic lives in scripts/lib/date_weekday_guard.py; this is the shell wrapper.
#
# Usage (source, then call with the exact outbound text):
#   source "$ORCH_DIR/scripts/lib/date_weekday_guard.sh"
#   _date_weekday_guard "$TEXT" || exit 6
#
# RULE (orch-console #58089):
#   * CONFIRMED mismatch (guard rc=1 AND a "REFUSED:" line) -> return 6, the send is refused.
#   * ANYTHING ELSE non-clean (rc=3 crash/timeout, 127 python missing, 2 script missing, an
#     import/syntax error) -> FAIL OPEN: loud stderr warning, page orch-console in the
#     BACKGROUND (never delays the send), return 0 so the send proceeds. A broken safety
#     check must never become an outage of every send.
# Text goes to python via a pipe on stdin — never argv, never a here-string (bash 3.2 backs
# those with a temp file). Invoked by FILE PATH so the caller's cwd can't swap the module.
# bash 3.2 compatible.
_date_weekday_guard() {
  local text="$1"
  local orch="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
  local py="$orch/.venv/bin/python3"
  local rc=0 err=""
  err="$(printf '%s' "$text" | "$py" "$orch/scripts/lib/date_weekday_guard.py" 2>&1 >/dev/null)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  if [ "$rc" -eq 1 ]; then
    case "$err" in
      REFUSED:*)
        printf '%s\n' "$err" >&2
        echo "ERROR: send BLOCKED by the weekday/date guard — nothing was sent." >&2
        return 6 ;;
    esac
  fi
  # ── guard CRASHED: fail OPEN + page ────────────────────────────────────────────────
  local src; src="$(basename -- "${0:-unknown}")"
  echo "WARNING: weekday/date guard CRASHED (rc=$rc) in $src — sending UNGUARDED; paging orch-console." >&2
  [ -n "$err" ] && printf '%s\n' "$err" | tail -n 15 >&2
  if [ -x "$py" ] && [ -f "$orch/scripts/lib/weekday_guard_pager.py" ]; then
    ( printf '%s' "$err" | "$py" "$orch/scripts/lib/weekday_guard_pager.py" \
        --source "$src" --error "guard rc=$rc" >/dev/null 2>&1 & ) || true
  else
    # No python / no pager on this host: we cannot reach the bus. Leave a durable record.
    local state="${WEEKDAY_GUARD_STATE_DIR:-$HOME/wingmen/fleet-health/state}"
    echo "WARNING: cannot page orch-console (no $py or pager) — logging to $state/weekday_guard_crash.log" >&2
    { mkdir -p "$state" && printf '%s CRASH source=%s rc=%s NO-PAGER (sent unguarded)\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$src" "$rc" >> "$state/weekday_guard_crash.log"; } 2>/dev/null || true
  fi
  return 0
}
