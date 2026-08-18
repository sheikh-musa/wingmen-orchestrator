#!/usr/bin/env bash
# safe_merge.sh — fail-closed PR merge. Merges ONLY when EVERY check on the PR has
# completed and succeeded; refuses (loudly) on pending/failed/skipped/zero-checks/
# API-error. Works where branch protection is unavailable (private free repos).
# Thin shim over scripts/lib/safe_merge.py — the testable core.
#
#   scripts/safe_merge.sh --repo sheikh-musa/ihsanos 332
#   scripts/safe_merge.sh --repo o/r 332 --method merge
#
# Do NOT use `gh pr merge --auto` for client-prod merges — it gates only on
# REQUIRED checks, which a private-free repo cannot define. Use this instead.
set -euo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ORCH_DIR/.venv/bin/python3"
[ -x "$PY" ] || PY="python3"

cd "$ORCH_DIR"
exec "$PY" -m scripts.lib.safe_merge "$@"
