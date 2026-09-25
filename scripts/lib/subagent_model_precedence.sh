#!/usr/bin/env bash
# Canonical CONDITIONAL subagent-model tier (#42821, op#22298 cost-rollout, Nazim gate
# #42793 cond-5, hardened per the #152 review). Sourced by scripts/launch_dangerous_cc.sh
# AND exercised directly by tests/test_subagent_model_precedence.py, so the SHIPPED
# resolution is the TESTED resolution (gate-test != shipped-path).
#
# WHAT: decide whether to export CLAUDE_CODE_SUBAGENT_MODEL for this lane so its subagents
# run on a cheaper model (e.g. Haiku for the scholar pilot half). Mirrors the main-model
# cascade (model_precedence.sh), keyed on the tmux session, DEFAULT-OFF/opt-in.
#
# Precedence (highest first):
#   CLAUDE_CODE_SUBAGENT_MODEL env  >  .<session>_subagent_model  >  .fleet_subagent_model  >  (unset)
#
# TWO FAIL-CLOSED guards from the #152 review (they compound, so both matter):
#  (1) UNRESOLVED SESSION: the launcher resolves the session via the SHARED resolver
#      (lane_session.sh). If it comes back EMPTY (detached launch, no TMUX_PANE/LANE_SESSION),
#      we CANNOT prove the lane isn't a CAI-1170 auditor -> fail CLOSED (export nothing, and
#      scrub any inherited value), warn LOUD. (An empty session used to make is_auditor_lane
#      false and SKIP the clamp — the bug.)
#  (2) INHERITED ENV: for an auditor (or an unresolved session) it is not enough to "emit
#      nothing" — the child would INHERIT a CLAUDE_CODE_SUBAGENT_MODEL already in the parent
#      env (the escape-hatch tier, or a tmux global). So the shipped entry EXPLICITLY `unset`s
#      it. That is why the shipped entry (apply_subagent_model) is side-effecting and sourced,
#      not a `$(...)`-captured echo.

# Auditor-lane SSOT (CAI-1170) — the SAME carve-out the model cascade uses, so a full
# auditor's audit subagents can NEVER be silently downgraded to a cheap model.
source "$(dirname "${BASH_SOURCE[0]}")/auditor_lanes.sh"

# _resolve_subagent_model_raw <session> <orch_dir> [fleet_file] — the tiered cascade, NO
# clamp, NO side effects. Echoes "<model><TAB><tier>" or NOTHING. Unit-testable in a subshell.
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

    # Tier 4 — DEFAULT-OFF: echo nothing.
    return 0
}

# apply_subagent_model <session> <orch_dir> — the SHIPPED, side-effecting entry. MUST be
# called SOURCED (it export/unsets in the caller's shell). Applies the fail-closed guards
# above, then the cascade. Prints a LOUD line to stderr whenever it scrubs/clamps.
apply_subagent_model() {
    local session="$1" orch_dir="$2"

    # Guard (1) — unresolved session: can't prove non-auditor -> fail closed + scrub inherited.
    if [ -z "$session" ]; then
        if [ -n "${CLAUDE_CODE_SUBAGENT_MODEL:-}" ]; then unset CLAUDE_CODE_SUBAGENT_MODEL; fi
        echo "subagent-model: tmux session UNRESOLVED — failing CLOSED (CLAUDE_CODE_SUBAGENT_MODEL unset; cannot prove this lane is not a CAI-1170 auditor). Set LANE_SESSION or launch inside a pane." >&2
        return 0
    fi

    # Guard (2) — CAI-1170 auditor: never a downgraded subagent model. Scrub ANY inherited value.
    if is_auditor_lane "$session"; then
        if [ -n "${CLAUDE_CODE_SUBAGENT_MODEL:-}" ]; then
            echo "subagent-model: CAI-1170 AUDITOR CLAMP — $session; unsetting inherited CLAUDE_CODE_SUBAGENT_MODEL (auditor subagents stay on the lane model)" >&2
            unset CLAUDE_CODE_SUBAGENT_MODEL
        fi
        return 0
    fi

    # Non-auditor, resolved session: run the cascade.
    local out model tier
    out="$(_resolve_subagent_model_raw "$session" "$orch_dir")"
    model="${out%%$'\t'*}"; tier="${out#*$'\t'}"
    if [ -n "$model" ]; then
        export CLAUDE_CODE_SUBAGENT_MODEL="$model"
        echo "▶ Subagent model: ${model} (via ${tier})"
    fi
    # else DEFAULT-OFF: leave the env as-is (a non-auditor may keep an inherited value via the
    # tier-1 escape hatch, which the cascade above would have returned + re-exported).
    return 0
}
