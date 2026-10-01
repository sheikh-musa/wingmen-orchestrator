#!/usr/bin/env bash
# secret_hash_sweep_run.sh — recurring driver for secret_hash_sweep.py (orch-console P1 #48640).
#
# Periodically re-sweeps recently-touched Claude Code transcripts for any REAL secret value
# (every value in this host's .env files) that leaked in since the last run — catching what
# the regex scanner (nervous_system.secret_redact) misses: bot tokens, DSN passwords, client
# keys. It builds its manifest FRESH from the live .env files each run (never a stale bundle).
#
# SAFETY / ARMING (matches the fleet's detect-ungated / destructive-action-armed shape):
#   * DEFAULT = DETECT + ALERT: runs --dry-run; if ANY secret span is found it pages
#     orch-console (P1) with COUNTS ONLY (never a value) and exits non-zero. Nothing is edited.
#   * ARMED (SECRET_SWEEP_ARM=1) = EXECUTE: redacts in place (size-preserving, reversible via
#     per-run 0600 backups), then pages a count-only summary. Arm only after review.
# Never prints a secret value (the tool identifies everything by sha256[:8]).
set -uo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ORCH_DIR/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"
WINDOW="${SECRET_SWEEP_WINDOW:-}"   # EMPTY = ALL dates (orch-console #48734: older transcripts hold secrets too; hash match is cheap). Set e.g. "30 hours ago" to window.
BAK_ROOT="${SECRET_SWEEP_BAK_ROOT:-$HOME/secret-sweep-backups}"
STAMP="$(date -u +%Y%m%dT%H%MZ)"

# Real .env files on THIS host (skip *.example / *.sample — placeholders).
ENVS=()
for g in "$HOME"/wingmen/orchestrator/.env "$HOME"/wingmen/orchestrator/.env.bak* \
         "$HOME"/wingmen/orchestrator/.env.pre-rotation* "$HOME"/wingmen/projects/*/.env.local \
         "$HOME"/wingmen/projects/*/.env; do
  case "$g" in *.example|*.sample) continue;; esac
  [ -f "$g" ] && ENVS+=("$g")
done
[ "${#ENVS[@]}" -gt 0 ] || { echo "secret_sweep_run: no .env files found — REFUSE" >&2; exit 3; }

# Transcripts to scan: ALL by default (every date, incl. subagent dirs — find recurses);
# only windowed when SECRET_SWEEP_WINDOW is set.
# NB: bash 3.2 (macOS /bin/bash, what launchd runs) has no `mapfile` — build the array by hand.
SCANS=()
if [ -n "$WINDOW" ]; then
  while IFS= read -r _f; do [ -n "$_f" ] && SCANS+=("$_f"); done \
    < <(find "$HOME/.claude/projects" -name '*.jsonl' -newermt "$WINDOW" 2>/dev/null)
else
  while IFS= read -r _f; do [ -n "$_f" ] && SCANS+=("$_f"); done \
    < <(find "$HOME/.claude/projects" -name '*.jsonl' 2>/dev/null)
fi
[ "${#SCANS[@]}" -gt 0 ] || { echo "secret_sweep_run: no transcripts — nothing to do"; exit 0; }

REPORT="$("$PY" "$ORCH_DIR/scripts/secret_hash_sweep.py" --env "${ENVS[@]}" --scan "${SCANS[@]}" \
          $([ "${SECRET_SWEEP_ARM:-0}" = "1" ] && printf -- "--execute --backup-dir %s" "$BAK_ROOT/$STAMP" || printf -- "--dry-run") \
          --report-json 2>&1)" || true

# Pull count-only fields (never values) out of the JSON report.
read -r BEFORE AFTER FILES < <(printf '%s' "$REPORT" | "$PY" -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print("-1 -1 -1"); sys.exit(0)
reps=d.get("reports",[])
print(d.get("total_before",-1), d.get("total_after",-1), sum(1 for r in reps if r.get("matches_before")))
')

if [ "$BEFORE" = "-1" ]; then
  echo "secret_sweep_run: tool output unparseable — PAGING (fail loud)" >&2
  "$PY" "$ORCH_DIR/scripts/bus_send.py" --to orch-console --from cc-fleet-health --type blocker \
    --priority P1 --req --subject "secret-hash-sweep: run FAILED (unparseable output) — investigate" \
    <<<"secret_hash_sweep_run.sh could not parse the sweep report on $(hostname). Dead-man: a failed secret sweep is louder than a silent one." >/dev/null 2>&1 || true
  exit 4
fi

if [ "${BEFORE:-0}" -gt 0 ]; then
  MODE="$([ "${SECRET_SWEEP_ARM:-0}" = "1" ] && echo EXECUTE || echo DETECT)"
  "$PY" "$ORCH_DIR/scripts/bus_send.py" --to orch-console --from cc-fleet-health --type blocker \
    --priority P1 --req --subject "secret-hash-sweep [$MODE] on $(hostname): $BEFORE secret span(s) in $FILES transcript(s) (after=$AFTER)" \
    <<EOF >/dev/null 2>&1 || true
secret-hash-sweep found $BEFORE real-secret span(s) across $FILES recent transcript(s) on $(hostname) [$MODE].
after=$AFTER. Counts only — no values. $([ "$MODE" = DETECT ] && echo "DETECT mode: nothing was edited; arm SECRET_SWEEP_ARM=1 (after review) to auto-redact, or run the manual sweep." || echo "EXECUTE mode: redacted in place, size-preserving, reversible (backups under $BAK_ROOT/$STAMP).")
EOF
  echo "secret_sweep_run [$MODE]: before=$BEFORE after=$AFTER files=$FILES (paged)"
  # In DETECT we surfaced a real finding; exit non-zero so launchd logs it as actionable.
  [ "$MODE" = DETECT ] && exit 5 || exit 0
fi

echo "secret_sweep_run: clean — 0 secret spans in ${#SCANS[@]} recent transcript(s)"
exit 0
