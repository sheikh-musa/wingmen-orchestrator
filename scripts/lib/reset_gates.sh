#!/usr/bin/env bash
# reset_gates.sh — the ONE shared safety-gate library for the unified reset +
# key-switch contract (op#14474). Every reset/switch path MUST source this and run
# its gates; a body type differs only in CONFIG (the registry row), never in WHETHER
# a gate runs. Design: reports/reset-keyswitch-safety-contract-design-20260818.md.
#
# PHASE 0: this lib + config/body_registry.json + tests only. NOTHING live sources it
# yet (zero behaviour change). Wiring the reset_*/switch_* scripts is Phase 1+.
#
# SPINE PRINCIPLE — FAIL-CLOSED: a gate that cannot EVALUATE must REFUSE, never pass.
# This is the antidote to today's recurring "a gauge reads green exactly when the
# thing it guards is failing" pattern (5 instances on 2026-08-18). A safety mechanism
# that cannot fail loudly is not a safety mechanism.

# Bumped whenever the gate SEMANTICS change. G7 (deploy-contract-version) compares a
# host's deployed value against what a caller requires, so a host running code that
# predates the current gates is REFUSED instead of silently acting unprotected — the
# fix for the 2026-08-18 VPS-divergence finding (the hub ran a fork missing the gates).
CONTRACT_VERSION=1

_RESET_GATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# G2 fresh-handoff lives in its own file (both this lib and standalone callers use it).
# shellcheck source=handoff_freshness.sh
. "$_RESET_GATES_DIR/handoff_freshness.sh"

# Default registry path (resolved relative to this lib: scripts/lib/ -> repo/config/).
RESET_GATES_REGISTRY="${RESET_GATES_REGISTRY:-$_RESET_GATES_DIR/../../config/body_registry.json}"

# --- G7: deploy-contract-version -------------------------------------------------
# gate_contract_version <required> [actual=CONTRACT_VERSION]
# REFUSE (return 7) when the deployed contract is older than required OR unreadable.
gate_contract_version() {
  local required="$1"
  # Distinguish "actual omitted" (check the LOCAL lib version — a valid self-check)
  # from "actual passed but empty" (the target's version was UNREADABLE — must REFUSE,
  # never fall back to the local version, which would fail OPEN over an unprotected host).
  local actual
  if [ "$#" -ge 2 ]; then actual="$2"; else actual="$CONTRACT_VERSION"; fi
  if ! printf '%s' "$required" | grep -qE '^[0-9]+$'; then
    echo "[reset-gates] DEPLOY-CONTRACT FAIL — required version '$required' is not a number; cannot evaluate -> REFUSING (fail-closed)." >&2
    return 7
  fi
  if ! printf '%s' "$actual" | grep -qE '^[0-9]+$'; then
    echo "[reset-gates] DEPLOY-CONTRACT FAIL — deployed version '${actual:-<none>}' unreadable; the target may run pre-contract code -> REFUSING (fail-closed)." >&2
    return 7
  fi
  if [ "$actual" -lt "$required" ]; then
    echo "[reset-gates] DEPLOY-CONTRACT FAIL — deployed contract v${actual} < required v${required}. This host runs code that predates the safety gates; REFUSING." >&2
    return 7
  fi
  echo "[reset-gates] deploy-contract OK — v${actual} >= required v${required}."
  return 0
}

# --- Q3: per-gate force flags (+ --force-all), each logged separately -------------
# A single blunt override is exactly how the 2026-08-18 hub-clear happened: override
# one gate, silently override the stale-handoff gate too. So force is PER-GATE.
RESET_FORCE_ALL=0
RESET_FORCE_BUSY=0
RESET_FORCE_STALE=0
reset_gates_parse_force() {
  RESET_FORCE_ALL=0; RESET_FORCE_BUSY=0; RESET_FORCE_STALE=0
  local a
  for a in "$@"; do
    case "$a" in
      --force-all)   RESET_FORCE_ALL=1 ;;
      --force-busy)  RESET_FORCE_BUSY=1 ;;
      --force-stale) RESET_FORCE_STALE=1 ;;
      --force)
        RESET_FORCE_ALL=1
        echo "[reset-gates] WARNING: blunt --force overrides EVERY gate (busy AND stale AND ...). Prefer --force-busy / --force-stale — this blunt override is how the 2026-08-18 hub-clear happened." >&2 ;;
    esac
  done
}
# force_for <gate> -> 0 if that specific gate is forced. Each check is separately
# auditable: the caller logs WHICH gate it overrode.
force_for() {
  [ "$RESET_FORCE_ALL" = 1 ] && return 0
  case "$1" in
    busy)  [ "$RESET_FORCE_BUSY" = 1 ] && return 0 ;;
    stale) [ "$RESET_FORCE_STALE" = 1 ] && return 0 ;;
  esac
  return 1
}

# --- registry lookup (dep-free via python3) --------------------------------------
# registry_field <body> <field> [registry_path] -> prints value, returns non-zero
# (fail-closed) if the body or field is absent / the registry is unreadable.
registry_field() {
  local body="$1" field="$2" reg="${3:-$RESET_GATES_REGISTRY}"
  python3 - "$reg" "$body" "$field" <<'PY'
import json, sys
reg, body, field = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(reg) as f:
        d = json.load(f)
except Exception as e:
    sys.stderr.write(f"[reset-gates] registry unreadable ({reg}): {e}\n"); sys.exit(7)
bodies = d.get("bodies", {})
if body not in bodies:
    sys.stderr.write(f"[reset-gates] body '{body}' not in registry -> REFUSE (fail-closed)\n"); sys.exit(3)
row = bodies[body]
if field not in row:
    sys.stderr.write(f"[reset-gates] field '{field}' missing for body '{body}' -> REFUSE (fail-closed)\n"); sys.exit(3)
v = row[field]
print("" if v is None else v)
PY
}

# registry_bodies [registry_path] -> lists real body ids (excludes _-prefixed templates)
registry_bodies() {
  local reg="${1:-$RESET_GATES_REGISTRY}"
  python3 - "$reg" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    sys.stderr.write(f"[reset-gates] registry unreadable: {e}\n"); sys.exit(7)
for k in d.get("bodies", {}):
    if not k.startswith("_"):
        print(k)
PY
}
