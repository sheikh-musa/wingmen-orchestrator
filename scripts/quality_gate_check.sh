#!/usr/bin/env bash
# quality_gate_check.sh — SHADOW-mode, OBSERVATION-ONLY quality gate probe.
#
# Head of Quality, Phase 2b (reports/head-of-quality-spec.md §8). This is a thin
# wrapper that: (1) gathers the git-derivable evidence for the CURRENT repo,
# (2) pipes it into the Phase-2 evaluator in SHADOW mode, (3) appends the verdict
# JSON to logs/quality_gate.log, and (4) ALWAYS exits 0.
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ THIS IS NOT A GATE. It observes; it never blocks.                        │
# │   - It is NOT installed into any git hook, merge, or deploy path.        │
# │   - It runs the evaluator in --mode shadow, which NEVER enforces.        │
# │   - It exits 0 unconditionally — it can never break a ship.              │
# │ Wiring this into a pre-commit/pre-merge/pre-deploy hook is a FUTURE,     │
# │ cai-gated step (spec §8 Phase 2+). Do not do it here.                    │
# └─────────────────────────────────────────────────────────────────────────┘
#
# The evidence is only the three checks git can settle honestly
# (committed-on-branch, migrations-tracked, ancestor-of-origin-main); every other
# floor check is absent and the evaluator marks it `unproven` — the honest
# dead-man default, not a faked pass.
#
# Usage:
#   scripts/quality_gate_check.sh <change-class>
# e.g.
#   scripts/quality_gate_check.sh docs-copy
#   scripts/quality_gate_check.sh ui-frontend
#
# Change class is passed straight to the evaluator; unknown -> strictest default.

set -uo pipefail   # NOTE: deliberately NOT `-e` — a probe must never abort a caller.

ORCH_DIR="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
LOG_DIR="$ORCH_DIR/logs"
LOG_FILE="$LOG_DIR/quality_gate.log"

CHANGE_CLASS="${1:-}"
if [ -z "$CHANGE_CLASS" ]; then
  echo "usage: quality_gate_check.sh <change-class>  (observation-only; always exits 0)" >&2
  exit 0
fi

# Gather evidence for the repo this script is run inside (its own working dir).
REPO_DIR="$(pwd)"

# Fall back to plain `python3` if the venv is absent (still observation-only).
if [ ! -x "$VENV_PY" ]; then
  VENV_PY="$(command -v python3 || true)"
fi
if [ -z "$VENV_PY" ]; then
  echo "quality_gate_check: no python available — skipping (observation-only)" >&2
  exit 0
fi

mkdir -p "$LOG_DIR" 2>/dev/null || true

# evidence (git reads) | evaluator (shadow, never blocks). PYTHONPATH so the
# nervous_system package resolves regardless of caller cwd.
VERDICT="$(
  PYTHONPATH="$ORCH_DIR" "$VENV_PY" -m nervous_system.quality_gate_evidence "$REPO_DIR" \
    | PYTHONPATH="$ORCH_DIR" "$VENV_PY" -m nervous_system.quality_gate \
        --class "$CHANGE_CLASS" --mode shadow 2>/dev/null
)" || true

if [ -n "${VERDICT:-}" ]; then
  # One compact JSON line per run, with a wall-clock stamp, appended to the log.
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"ts":"%s","repo":"%s","change_class":"%s","verdict":%s}\n' \
    "$TS" "$REPO_DIR" "$CHANGE_CLASS" \
    "$(printf '%s' "$VERDICT" | tr -d '\n')" \
    >> "$LOG_FILE" 2>/dev/null || true
  # Echo to stdout too, for an interactive human reading a shadow run.
  printf '%s\n' "$VERDICT"
fi

# ALWAYS succeed. A shadow probe can never be the reason a ship fails.
exit 0
