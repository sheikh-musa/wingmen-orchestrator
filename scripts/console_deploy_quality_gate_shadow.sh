#!/usr/bin/env bash
# console_deploy_quality_gate_shadow.sh <content-hash> <deploy-dir>
#
# Shadow-mode nervous_system/quality_gate.py consumer for deploy_console.sh (bus
# #43108 proposal, GO'd bus #43109 2026-09-24 with 4 conditions — this script IS
# the build). Phase 2 quality_gate.py in shadow mode is a pure observer: it can
# never block a deploy by construction (should_block() is only True in `block`
# mode). This wrapper adds its OWN guarantee on top of that: it ALWAYS exits 0,
# no matter what fails inside, so a broken evaluator/evidence-builder can never
# turn into a deploy_console.sh failure (GO condition #3 — "wrap the shadow call
# so an evaluator exception logs and the deploy continues, never blocks/crashes").
# See tests/test_console_deploy_quality_gate_shadow.py for the proof.
#
# Writes the verdict to <deploy-dir>/quality-gate-verdict.json (GO condition #2 —
# NOT deploy-log.txt, which stays the real-deploy record). Any error goes to
# <deploy-dir>/quality-gate.err, never to this script's exit code.
#
# Evidence honesty (GO condition #1) lives in scripts/console_deploy_gate_evidence.py
# — only checks deploy_console.sh's own 4 gates can actually attest to are
# populated; everything else deploy-prod's full ihsan floor (G1-G10) requires is
# left absent, not fabricated as "pass". Surfacing that gap is the point.
set -uo pipefail
HASH="${1:?usage: console_deploy_quality_gate_shadow.sh <content-hash> <deploy-dir>}"
DIR="${2:?usage: console_deploy_quality_gate_shadow.sh <content-hash> <deploy-dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$DIR" 2>/dev/null
ERR="$DIR/quality-gate.err"
OUT="$DIR/quality-gate-verdict.json"
PY="$ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

if [ -n "$PY" ]; then
  (
    cd "$ROOT" && \
    "$PY" scripts/console_deploy_gate_evidence.py "$HASH" "$DIR" \
      | PYTHONPATH="$ROOT" "$PY" -m nervous_system.quality_gate --class deploy-prod --mode shadow \
        >"$OUT"
  ) 2>"$ERR"
  rc=$?
else
  echo "no python3 found" >"$ERR"
  rc=1
fi

if [ "$rc" -eq 0 ] && [ -s "$OUT" ]; then
  WB=$(grep -o '"would_block": *[a-z]*' "$OUT" 2>/dev/null | head -1 | grep -oE '[a-z]+$')
  echo "        shadow quality-gate: would_block=${WB:-?} (observation only) -> $OUT"
else
  echo "        shadow quality-gate: evaluator error (non-blocking, deploy continues) — see $ERR" >&2
fi

# GO condition #3, enforced here (not just by quality_gate.py's own shadow
# contract): this wrapper's own exit code is NEVER non-zero. deploy_console.sh
# can call it unconditionally between gate 4 and the deploy step.
exit 0
