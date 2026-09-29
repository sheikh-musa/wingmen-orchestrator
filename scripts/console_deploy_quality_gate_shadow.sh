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
#
# HARD TIMEOUT (orch-console review #45683): always-exits-0 only covers a clean
# failure — a stuck git call or an import deadlock in quality_gate.py/the evidence
# builder would hang this SCRIPT, and deploy_console.sh calls it synchronously, so
# a hang here would hang the deploy. macOS ships no `timeout(1)`, so the pipeline
# runs backgrounded and is polled + killed (whole process tree, since bash gives a
# backgrounded job no process-group isolation here) after
# $QUALITY_GATE_SHADOW_TIMEOUT_SEC (default 60s). $QUALITY_GATE_SHADOW_PYTHON lets
# a test point PY at a controllable fake interpreter instead of the real
# $ROOT/.venv/bin/python3 (which always exists in this repo and would otherwise
# mask a hang/broken-interpreter test) — unset in production, no behavior change.
set -uo pipefail
HASH="${1:?usage: console_deploy_quality_gate_shadow.sh <content-hash> <deploy-dir>}"
DIR="${2:?usage: console_deploy_quality_gate_shadow.sh <content-hash> <deploy-dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$DIR" 2>/dev/null
ERR="$DIR/quality-gate.err"
OUT="$DIR/quality-gate-verdict.json"
TIMEOUT_SEC="${QUALITY_GATE_SHADOW_TIMEOUT_SEC:-60}"
PY="${QUALITY_GATE_SHADOW_PYTHON:-}"
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  PY="$ROOT/.venv/bin/python3"
  [ -x "$PY" ] || PY="$(command -v python3 || true)"
fi

_kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do _kill_tree "$child"; done
  kill -9 "$pid" 2>/dev/null
}

timed_out=0
if [ -n "$PY" ]; then
  (
    cd "$ROOT" && \
    "$PY" scripts/console_deploy_gate_evidence.py "$HASH" "$DIR" \
      | PYTHONPATH="$ROOT" "$PY" -m nervous_system.quality_gate --class deploy-prod --mode shadow \
        >"$OUT"
  ) 2>"$ERR" &
  pid=$!
  waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$TIMEOUT_SEC" ]; then
      timed_out=1
      _kill_tree "$pid"
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid" 2>/dev/null
  rc=$?
  if [ "$timed_out" -eq 1 ]; then
    echo "shadow quality-gate: TIMED OUT after ${TIMEOUT_SEC}s (killed) — deploy continues" >>"$ERR"
    rc=124
  fi
else
  echo "no python3 found" >"$ERR"
  rc=1
fi

if [ "$timed_out" -eq 0 ] && [ "$rc" -eq 0 ] && [ -s "$OUT" ]; then
  WB=$(grep -o '"would_block": *[a-z]*' "$OUT" 2>/dev/null | head -1 | grep -oE '[a-z]+$')
  echo "        shadow quality-gate: would_block=${WB:-?} (observation only) -> $OUT"
elif [ "$timed_out" -eq 1 ]; then
  echo "        shadow quality-gate: TIMED OUT after ${TIMEOUT_SEC}s (non-blocking, deploy continues) — see $ERR" >&2
else
  echo "        shadow quality-gate: evaluator error (non-blocking, deploy continues) — see $ERR" >&2
fi

# GO condition #3, enforced here (not just by quality_gate.py's own shadow
# contract): this wrapper's own exit code is NEVER non-zero. deploy_console.sh
# can call it unconditionally between gate 4 and the deploy step.
exit 0
