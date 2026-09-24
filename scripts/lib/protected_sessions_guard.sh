# protected_sessions_guard.sh — shared bash helper: resolve the tmux sessions a
# --live model flip (scripts/fleet_model.sh) must never touch, from the shared
# nervous_system.protected_agents.protected_tmux_sessions() registry, MINUS
# $AUDITOR_LANES (cc-quality/cc-storefront get their own separate opus-pin
# carve-out, not a blanket core-brain skip). Kept in its own file, not inlined
# in fleet_model.sh, so it is independently unit-testable via subprocess
# (tests/test_fleet_model_core_lanes.py) without touching live tmux/DB state.
#
# Requires: $VENV_PY and $AUDITOR_LANES already set by the caller (source
# lib/auditor_lanes.sh first).
#
# op#42896/#42909 P1, bus #43051 (2026-09-24): orch-console found the ORIGINAL
# version of this logic (inlined in fleet_model.sh) fail-OPEN on a registry
# read that succeeds but is short a name -- e.g. the CLI printing only
# "fleet-console" (no empty-output check catches that, since the string isn't
# empty) would have let --live send /model into cai/orch/nazim/fleet-health.
# The Python-side fix (nervous_system/protected_agents.py's
# _safe_registry_rows core-floor check) already prevents the CLI itself from
# ever emitting that shape again, but this function keeps an INDEPENDENT
# defense-in-depth check for the two non-negotiable strategic brains (cai,
# orch) — never trust a result missing either, regardless of why.

core_lanes_or_refuse() {
    local all_protected
    all_protected="$("$VENV_PY" -m nervous_system.protected_agents sessions 2>/dev/null)" || {
        echo "ERROR: could not read the protected-sessions registry — refusing (fail-closed, nothing flipped)" >&2
        return 5
    }
    [ -n "$all_protected" ] || {
        echo "ERROR: protected-sessions registry returned EMPTY — refusing (fail-closed, nothing flipped)" >&2
        return 5
    }

    local core_lanes="" s
    for s in $all_protected; do
        printf '%s ' $AUDITOR_LANES | grep -Fqw -- "$s" || core_lanes="$core_lanes $s"
    done

    local must
    for must in cai orch; do
        printf '%s ' $core_lanes | grep -qw "$must" || {
            echo "ERROR: protected-sessions result is missing '$must' (got: '$all_protected') — refusing (fail-closed, nothing flipped)" >&2
            return 5
        }
    done

    printf '%s\n' "$core_lanes"
}
