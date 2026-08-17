#!/usr/bin/env bash
# Canonical lane MODEL-precedence cascade (#24392). Sourced by
# scripts/launch_dangerous_cc.sh AND exercised directly by tests/test_model_
# precedence.py, so the SHIPPED resolution is the TESTED resolution (gate-test !=
# shipped-path). Kept as a function (no side effects) so a test can source + call
# it with controlled inputs.
#
# THE FIX: a NEW worker lane with no per-body `.<session>_model` used to fall
# straight through to `.fleet_model` (a fleet-wide Sonnet flip) instead of its
# FAMILY's model — a silent capability downgrade. This inserts a per-GROUP tier
# `.group_default_model.<family>` between `.<session>_model` and `.fleet_model`,
# mirroring the token per-group tier (family derived by the SAME python resolver so
# the model group file keys off the identical <family> as the token group file).
#
# Precedence (highest first):
#   MODEL env > .<session>_model > .group_default_model.<family> > .fleet_model > opus
# With NO group file present the result is byte-identical to the pre-fix behaviour.
#
# resolve_lane_model <session> <orch_dir> <venv_py> <fleet_model_file> <opus_default>
#   reads MODEL from the environment; echoes "<model><TAB><tier>" (tier = which file
#   won, for the boot banner). Fail-OPEN: a resolver error / unreadable / empty file
#   at any tier just falls through to the next lower tier.

resolve_lane_model() {
    local session="$1" orch_dir="$2" venv_py="$3" fleet_file="$4" opus_default="$5"

    # Tier 1 — explicit MODEL env override (wins over every file tier).
    if [ -n "${MODEL:-}" ]; then
        printf '%s\t%s\n' "$MODEL" "MODEL env"
        return 0
    fi

    # Tier 2 — per-body pointer `.<session>_model` (an operator's per-lane pick that
    # STICKS across reboots). Empty/whitespace file -> fall through.
    if [ -n "$session" ] && [ -r "$orch_dir/.${session}_model" ]; then
        local bm
        bm="$(tr -d '[:space:]' < "$orch_dir/.${session}_model")"
        if [ -n "$bm" ]; then
            printf '%s\t%s\n' "$bm" ".${session}_model"
            return 0
        fi
    fi

    # Tier 3 — per-GROUP `.group_default_model.<family>` (the #24392 fix), resolved by
    # the tested python module (worker lanes only; singleton/session bodies return
    # nothing). Fail-open: a resolver error -> empty -> fall through to .fleet_model
    # (the pre-fix direction, the SAFE one).
    if [ -n "$session" ]; then
        local gm _repo_root
        # Run python FROM the repo root (so `-m scripts.lib.*` imports) but read the
        # group file FROM orch_dir (passed explicitly). In production orch_dir IS the
        # repo root; keeping them distinct is what makes this testable + robust if a
        # caller ever runs with orch_dir != repo root.
        _repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
        gm="$(cd "$_repo_root" && "$venv_py" -m scripts.lib.lane_model_resolver --session "$session" --orch-dir "$orch_dir" 2>/dev/null || true)"
        if [ -n "$gm" ]; then
            printf '%s\t%s\n' "$gm" ".group_default_model.<family>"
            return 0
        fi
    fi

    # Tier 4 — fleet-wide `.fleet_model` (the operator's one-place Opus<->Sonnet flip
    # for token conservation). Empty/whitespace file -> fall through.
    if [ -r "$fleet_file" ]; then
        local fm
        fm="$(tr -d '[:space:]' < "$fleet_file")"
        if [ -n "$fm" ]; then
            printf '%s\t%s\n' "$fm" ".fleet_model"
            return 0
        fi
    fi

    # Tier 5 — hardcoded default.
    printf '%s\t%s\n' "$opus_default" "default (opus-4-8)"
    return 0
}
