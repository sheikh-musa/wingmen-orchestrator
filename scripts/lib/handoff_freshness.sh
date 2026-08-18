#!/usr/bin/env bash
# handoff_freshness.sh — the ONE shared "is the restore-point handoff fresh?" gate.
#
# WHY THIS IS A SHARED LIB: reset_nazim.sh grew a proper handoff-freshness gate
# (op#10967, FRESH_MAX=1800) but reset_orch.sh only ever checked the handoff
# EXISTED (`[ -f ]`), never its age. On 2026-08-18 that gap cleared the hub against
# a 4h33m-stale handoff — a fresh body would have booted from a stale board. A gate
# that lives inline in one sibling and is missing from another is exactly how safety
# drifts; putting the single definition here means neither script can silently lack
# it again.
#
# require_fresh_handoff <path> [max_sec] [force]
#   Echoes a one-line verdict. Returns 0 when the handoff is present AND newer than
#   max_sec (default 1800 = 30 min), OR when force=1 (prints a LOUD warning but
#   proceeds). Returns non-zero (does NOT exit — the CALLER decides how to fail) when
#   the handoff is missing or stale and force is not set. Never sends keystrokes;
#   pure read of the file's mtime, cross-platform (BSD `stat -f` / GNU `stat -c`).

require_fresh_handoff() {
  local path="$1"
  local max_sec="${2:-1800}"
  local force="${3:-0}"
  local now mtime age
  now="$(date -u +%s)"

  if [ -z "$path" ] || [ ! -f "$path" ]; then
    if [ "$force" = 1 ]; then
      echo "[handoff-freshness] WARNING — restore point '${path:-<none>}' is MISSING — force=1, proceeding (fresh body may boot from nothing)." >&2
      return 0
    fi
    echo "[handoff-freshness] FAIL — restore point '${path:-<none>}' is MISSING. Write a fresh handoff first (a reset would boot from nothing)." >&2
    return 3
  fi

  mtime="$(stat -f %m "$path" 2>/dev/null || stat -c %Y "$path" 2>/dev/null || echo 0)"
  age=$(( now - mtime ))

  if [ "$age" -gt "$max_sec" ]; then
    if [ "$force" = 1 ]; then
      echo "[handoff-freshness] WARNING — '$path' is ${age}s old (> ${max_sec}s STALE) — force=1, proceeding against a stale board." >&2
      return 0
    fi
    echo "[handoff-freshness] FAIL — '$path' is ${age}s old (> ${max_sec}s stale). A fresh handoff must be written first; the fresh body would boot from a stale board. Set the force flag to override." >&2
    return 3
  fi

  echo "[handoff-freshness] OK — '$path' is ${age}s old (<= ${max_sec}s)."
  return 0
}
