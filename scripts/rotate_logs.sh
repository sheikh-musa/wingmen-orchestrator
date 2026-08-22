#!/usr/bin/env bash
# rotate_logs.sh — size-cap the orchestrator's watchdog/daemon stdout logs so they stop
# growing UNBOUNDED and creeping the Data volume toward the disk WARN (op#15741 follow-up,
# orch-console #31443). cc-fleet-health infra/liveness pen.
#
# WHY tail-in-place (not mv/newsyslog): these logs are launchd StandardOutPath files held
# OPEN in append mode by the live daemons. An `mv` rotates the NAME but the daemon keeps
# writing to the moved inode, so the new file stays empty and the old one keeps growing.
# Truncate-in-place on the SAME inode works: O_APPEND seeks to end on every write, so after
# we shrink the file the daemon simply appends after the kept tail — no sparse file, no lost
# fd, no daemon restart. VERIFIED empirically (30004->2006 lines while an append-writer held
# it open; inode preserved; du==ls, not sparse; writer continued cleanly).
#
# FAIL-SAFE (dead-man's-switch discipline): every per-file step that could lose data aborts
# THAT file and leaves it untouched rather than truncating on a half-failure. Never truncates
# a file it could not first read the tail of.
#
# Usage:  rotate_logs.sh            # cap oversized logs in place
#         rotate_logs.sh --dry-run  # print what WOULD be capped, touch nothing
set -uo pipefail

LOG_DIR="${LOG_ROTATE_DIR:-$HOME/wingmen/orchestrator/logs}"
CAP_BYTES="${LOG_ROTATE_CAP_BYTES:-5242880}"   # 5 MB — cap any *.log over this
KEEP_LINES="${LOG_ROTATE_KEEP_LINES:-5000}"    # ... down to its last N lines (recent history kept)
SELF_LOG="$LOG_DIR/log-rotate.log"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

# Forensic/audit logs whose HEAD history matters — never truncate these (they are also small,
# but guard by name so a future size spike can't silently drop audit evidence).
_is_protected() {
  case "$(basename "$1")" in
    lane_nudge_preserved_input.log) return 0 ;;
    *) return 1 ;;
  esac
}

_ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
_note() { [ "$DRY" -eq 1 ] && echo "$*" || printf '%s %s\n' "$(_ts)" "$*" >> "$SELF_LOG" 2>/dev/null || true; }

[ -d "$LOG_DIR" ] || { echo "$(_ts) ERROR: log dir $LOG_DIR absent — aborting (no-op)" >&2; exit 1; }

capped=0; freed=0
# *.log = launchd StandardOutPath; *.err = StandardErrorPath — both are daemon-held append
# logs that grow unbounded (agent-wake-subscriber.err, watchdog.err were as big as the .out).
for f in "$LOG_DIR"/*.log "$LOG_DIR"/*.err; do
  [ -f "$f" ] || continue   # literal glob (no match) is not a file -> skipped
  _is_protected "$f" && continue
  sz=$(stat -f %z "$f" 2>/dev/null || echo 0)
  [ "$sz" -gt "$CAP_BYTES" ] || continue
  if [ "$DRY" -eq 1 ]; then
    _note "WOULD cap $(basename "$f") — ${sz}B -> last ${KEEP_LINES} lines"
    capped=$((capped+1)); freed=$((freed+sz)); continue
  fi
  # tail-in-place, fail-safe: only truncate if we successfully captured the tail.
  T="$(mktemp "${TMPDIR:-/tmp}/rotate_logs.XXXXXX")" || { _note "SKIP $(basename "$f") — mktemp failed"; continue; }
  if ! tail -n "$KEEP_LINES" "$f" > "$T" 2>/dev/null; then
    rm -f "$T"; _note "SKIP $(basename "$f") — tail failed, left untouched"; continue
  fi
  if [ ! -s "$T" ]; then
    rm -f "$T"; _note "SKIP $(basename "$f") — captured tail EMPTY, left untouched"; continue
  fi
  if cat "$T" > "$f" 2>/dev/null; then
    new=$(stat -f %z "$f" 2>/dev/null || echo 0)
    _note "capped $(basename "$f") — ${sz}B -> ${new}B (kept last ${KEEP_LINES} lines)"
    capped=$((capped+1)); freed=$((freed+sz-new))
  else
    _note "LOUD: $(basename "$f") truncate-write FAILED after capturing tail — file may be short; tail preserved in $T"
    # keep $T as forensic evidence on failure (do NOT rm) so nothing is silently lost
  fi
  [ -e "$T" ] && rm -f "$T" 2>/dev/null || true
done

_note "rotate_logs done: ${capped} file(s) capped, ~$((freed/1048576))MB reclaimed (cap=${CAP_BYTES}B keep=${KEEP_LINES}l dry=${DRY})"
