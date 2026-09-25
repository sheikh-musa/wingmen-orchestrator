#!/usr/bin/env bash
# Canonical CONDITIONAL subagent-model tier (#42821, op#22298 cost-rollout, Nazim gate
# #42793 cond-5). Sourced by scripts/launch_dangerous_cc.sh AND exercised directly by
# tests/test_subagent_model_precedence.py, so the SHIPPED resolution is the TESTED
# resolution (gate-test != shipped-path). A pure function (no side effects) so a test
# can source + call it with controlled inputs.
#
# WHAT: decide whether to export CLAUDE_CODE_SUBAGENT_MODEL for this lane so its
# subagents run on a cheaper model (e.g. Haiku) — the scholar Haiku-subagent pilot half.
# Mirrors the main-model cascade (model_precedence.sh), keyed on the tmux session, but
# DEFAULT-OFF/opt-in: with nothing set the function echoes NOTHING and the launcher never
# exports the var, so behaviour is byte-identical to today (subagents inherit the lane's
# main model). NOT fleet-wide until an operator/hub writes a marker.
#
# Precedence (highest first):
#   CLAUDE_CODE_SUBAGENT_MODEL env  >  .<session>_subagent_model  >  .fleet_subagent_model  >  (unset)
# The env tier is the operator escape hatch (mirrors MODEL env in model_precedence.sh).
# Empty/whitespace markers are inert -> fall through. Fail-safe: unreadable/empty at any
# tier just falls to the next lower tier, ending at "unset" (the safe, no-op direction).
#
# resolve_subagent_model <session> <orch_dir> [fleet_file]
#   echoes "<model><TAB><tier>" (tier = which source won, for the boot banner), or NOTHING
#   when no tier applies OR when the CAI-1170 auditor clamp suppresses it.

# Auditor-lane SSOT (CAI-1170) — the SAME carve-out the model cascade uses, so a full
# auditor's audit subagents can NEVER be silently downgraded to a cheap model. One edit
# in auditor_lanes.sh reaches the main-model clamp AND this subagent clamp.
source "$(dirname "${BASH_SOURCE[0]}")/auditor_lanes.sh"

# _resolve_subagent_model_raw — the tiered cascade (no clamp). The PUBLIC entry
# resolve_subagent_model wraps this with the CAI-1170 auditor clamp.
_resolve_subagent_model_raw() {
    local session="$1" orch_dir="$2" fleet_file="${3:-$orch_dir/.fleet_subagent_model}"

    # Tier 1 — explicit CLAUDE_CODE_SUBAGENT_MODEL env (operator escape hatch; wins over files).
    if [ -n "${CLAUDE_CODE_SUBAGENT_MODEL:-}" ]; then
        printf '%s\t%s\n' "$CLAUDE_CODE_SUBAGENT_MODEL" "env"
        return 0
    fi

    # Tier 2 — per-body pointer `.<session>_subagent_model` (opt-in per lane; STICKS across
    # reboots). Empty/whitespace file -> fall through.
    if [ -n "$session" ] && [ -r "$orch_dir/.${session}_subagent_model" ]; then
        local bm
        bm="$(tr -d '[:space:]' < "$orch_dir/.${session}_subagent_model")"
        if [ -n "$bm" ]; then
            printf '%s\t%s\n' "$bm" ".${session}_subagent_model"
            return 0
        fi
    fi

    # Tier 3 — fleet-wide `.fleet_subagent_model` (STAGE-2 fleet flip; one place). Empty ->
    # fall through.
    if [ -r "$fleet_file" ]; then
        local fm
        fm="$(tr -d '[:space:]' < "$fleet_file")"
        if [ -n "$fm" ]; then
            printf '%s\t%s\n' "$fm" ".fleet_subagent_model"
            return 0
        fi
    fi

    # Tier 4 — DEFAULT-OFF: echo nothing (the launcher leaves CLAUDE_CODE_SUBAGENT_MODEL unset).
    return 0
}

# resolve_subagent_model — PUBLIC entry. Runs the cascade, then applies the CAI-1170
# FULL-AUDITOR CLAMP: cc-quality / cc-storefront render governance and their audit
# subagents (and the general-purpose builder subagents they spawn) must NEVER run on a
# downgraded model, regardless of which tier won (an env, a per-session marker, or a
# fleet flip). For an auditor the function suppresses ALL output (fail-closed = unset =
# subagents inherit the auditor's opus main model) and warns LOUD on stderr. Same stdout
# contract (<model>\t<tier>, or empty).
resolve_subagent_model() {
    local session="$1"
    local out model tier
    out="$(_resolve_subagent_model_raw "$@")"
    model="${out%%$'\t'*}"
    tier="${out#*$'\t'}"

    if is_auditor_lane "$session"; then
        if [ -n "$model" ]; then
            printf 'subagent_model_precedence: CAI-1170 AUDITOR CLAMP — %s had a subagent-model "%s" via %s; REFUSING (auditor subagents stay on the lane model)\n' \
                "$session" "$model" "$tier" >&2
        fi
        return 0   # emit nothing -> unset
    fi

    [ -n "$model" ] && printf '%s\t%s\n' "$model" "$tier"
    return 0
}
