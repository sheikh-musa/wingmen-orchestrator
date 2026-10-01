#!/usr/bin/env bash
# safe_merge.sh — squash-merge a PR ONLY when every check is green AND the head SHA is
# unchanged; refuse fail-closed otherwise (orch-console #48411). Closes the "merge while
# CI in_progress" hole that let PR#243 merge on a pending build. Thin wrapper over `gh` +
# the testable decision in scripts/lib/merge_gate.py. NOT for client-prod repos — those
# keep the ihsanos safe_merge.sh; this is the orchestrator's merge gate.
#
# Usage: scripts/safe_merge.sh --repo <owner/repo> <PR> [--dry-run] [--no-delete-branch]
#        [--allow-skipped <check>]...
set -uo pipefail

REPO=""; PR=""; DRYRUN=0; DELETE=1; ALLOW=()
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2;;
    --dry-run) DRYRUN=1; shift;;
    --no-delete-branch) DELETE=0; shift;;
    --allow-skipped) ALLOW+=("--allow-skipped" "${2:-}"); shift 2;;
    -*) echo "safe_merge: unknown flag $1" >&2; exit 2;;
    *) PR="$1"; shift;;
  esac
done
[ -n "$REPO" ] && [ -n "$PR" ] || { echo "usage: safe_merge.sh --repo <owner/repo> <PR> [--dry-run] [--no-delete-branch] [--allow-skipped <check>]..." >&2; exit 2; }

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ORCH_DIR/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"

# 1. PR must be OPEN + MERGEABLE.
VIEW="$(gh pr view "$PR" --repo "$REPO" --json state,mergeable,headRefOid 2>/dev/null)" \
  || { echo "safe_merge: gh pr view failed — REFUSE (fail-closed)" >&2; exit 3; }
STATE="$(printf '%s' "$VIEW" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["state"])')"
MERGEABLE="$(printf '%s' "$VIEW" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["mergeable"])')"
HEAD="$(printf '%s' "$VIEW" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["headRefOid"])')"
[ "$STATE" = "OPEN" ]       || { echo "safe_merge: PR #$PR state=$STATE (not OPEN) — REFUSE" >&2; exit 3; }
[ "$MERGEABLE" = "MERGEABLE" ] || { echo "safe_merge: PR #$PR mergeable=$MERGEABLE — REFUSE" >&2; exit 3; }

# 2. Every check green (fail-closed on pending/failure/zero) — the testable gate.
CHECKS="$(gh pr checks "$PR" --repo "$REPO" --json name,state 2>/dev/null)" \
  || { echo "safe_merge: gh pr checks failed — REFUSE (fail-closed; a PR with no measurable checks is not provably green)" >&2; exit 3; }
if ! printf '%s' "$CHECKS" | "$PY" "$ORCH_DIR/scripts/lib/merge_gate.py" "${ALLOW[@]}"; then
  echo "safe_merge: checks not green — REFUSE" >&2; exit 3
fi

# 3. Re-verify the head SHA is unchanged since we read the checks (no race).
HEAD2="$(gh pr view "$PR" --repo "$REPO" --json headRefOid -q .headRefOid 2>/dev/null)"
[ "$HEAD2" = "$HEAD" ] || { echo "safe_merge: head SHA changed ($HEAD -> ${HEAD2:-?}) since checks — REFUSE" >&2; exit 3; }

if [ "$DRYRUN" = "1" ]; then
  echo "safe_merge: DRY-RUN — PR #$PR OPEN+MERGEABLE, all checks green, head stable ($HEAD). Would squash-merge."
  exit 0
fi

# 4. Squash-merge, pinned to the verified head (gh refuses if it moved).
MERGE=(--squash --match-head-commit "$HEAD"); [ "$DELETE" = "1" ] && MERGE+=(--delete-branch)
gh pr merge "$PR" --repo "$REPO" "${MERGE[@]}" && echo "safe_merge: PR #$PR squash-merged at $HEAD."
