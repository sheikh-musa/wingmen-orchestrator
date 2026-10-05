#!/usr/bin/env bash
# Canonical lane PROVIDER-routing cascade (Musa op#26108 A/B harness, bus
# #53237/#53267). Sourced by scripts/launch_dangerous_cc.sh AND exercised
# directly by tests/test_provider_routing.py, so the SHIPPED routing is the
# TESTED routing (gate-test != shipped-path) — same pattern as
# model_precedence.sh.
#
# Generalizes the original inline GLM-only `case "$RESOLVED_MODEL" in glm-*)`
# arm (Musa op#24283/24299/24541) into a per-provider table, because
# Moonshot (Kimi), DeepSeek, and Alibaba DashScope (Qwen) each ship the exact
# same shape of integration z.ai already proved for GLM: a drop-in Anthropic
# Messages-API-compatible endpoint, selected via ANTHROPIC_BASE_URL +
# ANTHROPIC_AUTH_TOKEN. No new gateway/proxy — this is a mechanical extension
# of an already-shipped mechanism, not a new one.
#
# apply_provider_routing <resolved_model> <orch_dir> <venv_py> <agent_id> [fetch_script]
#
#   - resolved_model not matching any known provider prefix (every claude-*
#     lane, today's only case): returns 0, exports NOTHING. Byte-identical to
#     pre-this-change behavior — this is the regression guard's main claim.
#   - matching prefix + vault fetch succeeds: exports ANTHROPIC_BASE_URL,
#     ANTHROPIC_AUTH_TOKEN, ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL,
#     CLAUDE_CODE_SUBAGENT_MODEL; unsets CLAUDE_CODE_OAUTH_TOKEN(_OVERRIDE)
#     and ANTHROPIC_API_KEY so no Anthropic credential can reach a non-
#     Anthropic endpoint. Returns 0.
#   - matching prefix + vault fetch fails (empty key): prints FATAL to
#     stderr, exports NOTHING, returns 1. Fail-closed — the caller must abort
#     the boot rather than silently fall back to Anthropic/metered billing.
#
#   fetch_script defaults to provider_vault_fetch.py (the real vault read);
#   tests override it with a stub so no test call ever touches the vault.

# _provider_for_model <resolved_model>
# Echoes "label|base_url|vault_key|small_default|small_override_env" and
# returns 0 on a known prefix; returns 1 (echoes nothing) otherwise.
#
# small_default/small_override_env pick the model used for the Haiku slot:
#   ${!small_override_env} (if set) > small_default (if non-empty) >
#   resolved_model itself (never a guessed id for a provider with no
#   doc-confirmed cheap variant at generalization time).
#
# NOTE on model-id staleness: these vendors rotate model names fast (Kimi
# K2 -> K2.6/K2.7-code/K3; Qwen 3.6/3.7/3.8 in rapid succession). The
# base_url + vault_key per provider are the load-bearing, slow-moving part;
# the small_default ids are a best-effort-at-generalization-time fallback,
# not asserted current — confirm against each platform's live model list
# before relying on the Haiku slot for real benchmark numbers.
_provider_for_model() {
    case "$1" in
        glm-*)
            echo "z.ai GLM|https://api.z.ai/api/anthropic|GLM_CODING_KEY|glm-4.7|GLM_SMALL_MODEL"
            ;;
        kimi-*|moonshot-*)
            # api.moonshot.ai/anthropic confirmed (platform.kimi.ai docs + CC
            # integration guides); no doc-confirmed cheap/small id at
            # generalization time, so no small_default (falls through to
            # resolved_model itself).
            echo "Moonshot Kimi|https://api.moonshot.ai/anthropic|MOONSHOT_CODING_KEY||MOONSHOT_SMALL_MODEL"
            ;;
        deepseek-*)
            # api-docs.deepseek.com/guides/anthropic_api confirms
            # deepseek-flash as the cheap/fast model; deepseek-v4-pro is the
            # larger one.
            echo "DeepSeek|https://api.deepseek.com/anthropic|DEEPSEEK_CODING_KEY|deepseek-flash|DEEPSEEK_SMALL_MODEL"
            ;;
        qwen*)
            # Coding-Plan endpoint (subscription key, matches the
            # *_CODING_KEY vault-naming convention used for every other
            # provider here) — NOT the pay-as-you-go workspace-scoped URL.
            # qwen3.6-flash confirmed as a valid cheap model id.
            echo "Qwen (DashScope Coding Plan)|https://coding-intl.dashscope.aliyuncs.com/apps/anthropic|QWEN_CODING_KEY|qwen3.6-flash|QWEN_SMALL_MODEL"
            ;;
        *)
            return 1
            ;;
    esac
}

apply_provider_routing() {
    # PROVIDER_ROUTING_LABEL/_SMALL: deliberately GLOBAL (not local), not a
    # printf-to-stdout return — the launcher needs the exports below to land
    # in ITS shell, and `$(apply_provider_routing ...)` would run the whole
    # function in a subshell, discarding every export the instant the
    # subshell exits. A plain (unsubshelled) function call plus globals is
    # the only way to both export into the caller AND hand back the label
    # for its banner print. Cleared on every call so a no-match leaves no
    # stale label from a hypothetical earlier call in the same shell.
    PROVIDER_ROUTING_LABEL=""
    PROVIDER_ROUTING_SMALL=""

    local resolved_model="$1" orch_dir="$2" venv_py="$3" agent_id="$4"
    local fetch_script="${5:-$orch_dir/scripts/lib/provider_vault_fetch.py}"
    local row label base_url vault_key small_default small_override_env

    row="$(_provider_for_model "$resolved_model")" || return 0
    IFS='|' read -r label base_url vault_key small_default small_override_env <<< "$row"

    local key
    key="$(cd "$orch_dir" && AGENT_ID="$agent_id" "$venv_py" "$fetch_script" "$vault_key" "$label" 2>/dev/null || true)"

    if [ -z "$key" ]; then
        echo "FATAL: model ${resolved_model} needs ${vault_key} from the vault and it could not be read. Refusing to boot (no silent fallback to Anthropic)." >&2
        return 1
    fi

    unset CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE ANTHROPIC_API_KEY
    export ANTHROPIC_BASE_URL="$base_url"
    export ANTHROPIC_AUTH_TOKEN="$key"

    local override_val=""
    [ -n "$small_override_env" ] && override_val="${!small_override_env:-}"
    local small="${override_val:-${small_default:-$resolved_model}}"

    export ANTHROPIC_DEFAULT_OPUS_MODEL="$resolved_model"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="$resolved_model"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="$small"
    export CLAUDE_CODE_SUBAGENT_MODEL="$resolved_model"

    PROVIDER_ROUTING_LABEL="$label"
    PROVIDER_ROUTING_SMALL="$small"
    return 0
}
