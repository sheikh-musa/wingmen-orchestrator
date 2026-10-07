#!/usr/bin/env bash
# client_artifact_scan.sh — refuse a client-bound file (TG send, Drive upload, PDF/
# xlsx/docx report) whose RENDERED content carries a JS-serialization placeholder
# (undefined/null/NaN/[object), fleet-internal vocabulary (op#NNNNN, a bare 5-digit
# bus id, cc-<agent>, CAI-, orch-console/Nazim, wet-proof/silo/lane/bus row), or a
# stale PROPOSED label on a doc that claims to be applied.
#
# WHY (fable audit 2026-10-06 fork E, D1/D2): a client-facing workbook shipped with
# "undefinedp" x4 in its Period-Accounting line (#54400/#54497), and a separate
# render wrote the fleet bus id op#26543 into 4 client docs because the vocab scan
# covered src/ only, not the rendered output tree (#54497 self-catch). Logic lives
# in scripts/lib/client_artifact_scan.py (bus #58159 item 3); this is the shell
# wrapper every client-bound send/upload path sources.
#
# Usage (source, then call with the file about to be sent):
#   source "$ORCH_DIR/scripts/lib/client_artifact_scan.sh"
#   _client_artifact_scan "$FILE" || exit 7
#
# RULE (mirrors date_weekday_guard.sh, #58089):
#   * CONFIRMED violation (guard rc=1 AND a "REFUSED:" line) -> return 1, the send
#     is refused.
#   * ANYTHING ELSE non-clean (crash, missing python/deps, a file the scanner can't
#     parse) -> FAIL OPEN: loud stderr warning, return 0 so the send proceeds. A
#     broken scanner must never become an outage of every client send.
# Invoked by FILE PATH so the caller's cwd can't swap the module. bash 3.2 compatible.
_client_artifact_scan() {
  local file="$1"
  local orch="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
  local py="$orch/.venv/bin/python3"
  local rc=0 err=""
  err="$("$py" "$orch/scripts/lib/client_artifact_scan.py" "$file" 2>&1 >/dev/null)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  case "$err" in
    REFUSED:*)
      printf '%s\n' "$err" >&2
      echo "ERROR: send BLOCKED by the client artifact scanner — nothing was sent." >&2
      return 1 ;;
  esac
  # ── scanner CRASHED or file unreadable: fail OPEN + warn ──────────────────────────
  local src; src="$(basename -- "${0:-unknown}")"
  echo "WARNING: client artifact scanner did not run cleanly (rc=$rc) in $src for $file — sending UNSCANNED." >&2
  [ -n "$err" ] && printf '%s\n' "$err" | tail -n 15 >&2
  return 0
}
