# shellcheck shell=bash
# scripts/lib/lane_gcloud_isolation.sh
#
# PART A of the lane prod-data wall (op#29348 — "a lane read ADCDA prod a 3rd
# time after an explicit don't"; make it TRUE IN CODE).
#
# A worker lane boots with THREE ambient paths to cosem-adcda PROD data, all
# inherited from the operator's own shell/user config:
#   (1) gcloud's per-user `default` config  (~/.config/gcloud): core.project=
#       cosem-adcda-cb6d9 + the prod SA account -> any bare `gcloud` / ADC call
#       silently resolves PROD.
#   (2) GOOGLE_APPLICATION_CREDENTIALS set in ~/.zshenv -> points ADC straight
#       at the prod SA key file.
#   (3) the prod SA key FILE itself, readable by the same unix user (Part B's
#       PreToolUse guard closes the re-export of that file; this half closes the
#       ambient-default side).
#
# This function neuters (1) + (2) FOR WORKER-LANE SHELLS ONLY by:
#   - SETTING `GOOGLE_APPLICATION_CREDENTIALS` to a guaranteed-NON-EXISTENT
#     per-lane sentinel path (`<lane_cfg>/.no-prod-adc`) — NOT unsetting it.
#     WHY NOT UNSET (reviewer's hole, op#29348): when GAC is UNSET, Node's ADC
#     resolution FALLS BACK to the well-known per-user `$HOME/.config/gcloud`
#     ADC (application_default_credentials.json / credentials.db / …) and it
#     IGNORES CLOUDSDK_CONFIG entirely. So merely unsetting GAC does NOT stop
#     `initializeApp({})` from silently picking up the operator's PROD ADC. By
#     pointing GAC at a non-existent sentinel, ADC uses THAT file exclusively
#     and FAILS CLOSED with a credentials error — it never consults the
#     well-known prod path. (Part B's guard denies re-pointing GAC / reading the
#     well-known files, as a hard backstop.)
#   - pointing CLOUDSDK_CONFIG at a PER-LANE gcloud config dir seeded with a
#     clean, minimal active config that has NO project and NO account — so
#     `gcloud` (which DOES honour CLOUDSDK_CONFIG) has no implicit prod project.
#     Prod then needs an EXPLICIT `--project` + explicit creds, which Part B
#     refuses for a lane.
#
# SCOPE — worker lanes ONLY. NOT ~/.zshenv (that stays as the operator's own
# default), NOT the console, NOT CI:
#   - The CONSOLE (base == orch-console) is skipped: the console body is the
#     one that legitimately runs prod ops, and its config-write path (the
#     trainer-strip flow, 2026-10-10) must be UNAFFECTED. It also boots via a
#     SEPARATE launcher (boot_nazim.sh), never launch_dangerous_cc.sh, so it
#     never even reaches this call — the base-gate is belt-and-suspenders.
#   - CI has no CC_BASE_AGENT_ID (it runs pytest directly, not through the lane
#     launcher) -> an empty base is likewise skipped.
#
# Same discriminator as the PreToolUse guards (CC_BASE_AGENT_ID present AND
# base != orch-console == a worker lane). The function export/unsets in the
# CALLER's shell (it is SOURCED by launch_dangerous_cc.sh), so the launched
# claude child inherits the isolated env.
#
# FAIL-LOUD, never silent: GOOGLE_APPLICATION_CREDENTIALS is pointed at the
# non-existent sentinel FIRST (that cannot fail and fails-closed the primary
# prod-key / well-known-ADC vector); if the per-lane config dir cannot be
# seeded, the function returns non-zero so the launcher can warn LOUDLY — it
# does not swallow the failure behind `|| true`.

# apply_lane_gcloud_isolation <base_agent_id> <lane_id> [config_root]
#   base_agent_id : CC_BASE_AGENT_ID (the family); the gate reads this.
#   lane_id       : CC_AGENT_ID (the instance); names the per-lane config dir.
#   config_root   : parent dir for per-lane configs (default ~/.wingmen/lane-gcloud).
# Returns 0 on success (or when deliberately skipped: console / CI / empty base),
# 1 if a worker lane could not be isolated (caller must surface this LOUDLY).
apply_lane_gcloud_isolation() {
    local base_agent_id="${1:-}"
    local lane_id="${2:-}"
    local config_root="${3:-$HOME/.wingmen/lane-gcloud}"

    # Gate: worker lane == base present AND base != orch-console. Skip the
    # console and any non-lane (empty base = CI / bare shell) — exit 0, no-op.
    if [ -z "$base_agent_id" ] || [ "$base_agent_id" = "orch-console" ]; then
        return 0
    fi

    # (1) Per-lane gcloud config dir with NO project / NO account. Name it by
    # the instance id when present (true per-lane), else the family. This is
    # pure string construction (no I/O) so it cannot fail — we need lane_cfg to
    # derive the sentinel GAC path below, which must be set BEFORE any seeding
    # can fail.
    local lane_tag="${lane_id:-$base_agent_id}"
    local lane_cfg="$config_root/$lane_tag"

    # (2) FAIL CLOSED, not unset. Point GAC at a guaranteed-non-existent
    # per-lane sentinel. If we merely UNSET it, Node's ADC falls back to the
    # well-known $HOME/.config/gcloud prod ADC (ignoring CLOUDSDK_CONFIG); with
    # GAC pointed at a missing file, ADC errors out instead. We never create
    # this file. Setting a var cannot fail, so this closes the primary vector
    # even if the config seeding below fails.
    export GOOGLE_APPLICATION_CREDENTIALS="$lane_cfg/.no-prod-adc"

    if ! mkdir -p "$lane_cfg/configurations" 2>/dev/null; then
        # Could not build the isolation dir — do NOT point CLOUDSDK_CONFIG at a
        # dir we don't control, and do NOT fall back silently to the per-user
        # prod default. GAC is already pointed at the fail-closed sentinel;
        # signal the partial failure LOUDLY.
        return 1
    fi
    # Seed a clean minimal active config: an empty [core] section — no project,
    # no account. An absent config file would already mean "no implicit
    # project"; writing the explicit empty [core] makes the intent legible and
    # survives a lane running `gcloud config list`.
    if ! printf '[core]\n' > "$lane_cfg/configurations/config_default" 2>/dev/null; then
        return 1
    fi
    if ! printf 'default' > "$lane_cfg/active_config" 2>/dev/null; then
        return 1
    fi
    export CLOUDSDK_CONFIG="$lane_cfg"
    return 0
}
