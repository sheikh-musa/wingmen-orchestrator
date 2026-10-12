# shellcheck shell=bash
# scripts/lib/lane_env_allowlist.sh
#
# LEAST-PRIVILEGE LANE ENV (op#29348 part b, Nazim #64245/#64248/#64250).
#
# WHY: launch_dangerous_cc.sh sources the whole orchestrator .env with `set -a`,
# so EVERY lane (and every subagent it spawns) inherits all ~38 secrets. On
# 2026-10-12 a build subagent ran client functions against an emulator that
# LOGGED its inherited env, leaking ~34 orchestrator secrets into its transcript.
# Fix: launch `claude` from a SCRUBBED env (`env -i` + an explicit allow-list), so
# only the names below reach the claude process and its subagents. Everything
# else from .env is DROPPED (default-deny: a NEW secret added to .env later is
# auto-excluded until someone opts a lane into it).
#
# TWO allow-list tiers + a per-lane opt-in:
#   SAFE   — non-secret essentials (shell/term/paths/identity/CC flags/Part-A).
#            Generous on purpose: missing one of these breaks claude/hooks, and
#            none of them is a credential.
#   KEEP   — the minimal SECRETS lanes genuinely use fleet-wide. VERIFIED:
#            - substrate creds (DATABASE_URL 174x / SUPABASE_DB_URL 150x in the
#              lane scripts) — every lane talks to the substrate.
#            - GH_TOKEN — `gh auth status` shows the fleet's gh is logged in VIA
#              this env token (not a keyring), so dropping it breaks gh/PR/CI for
#              every lane. (Future hardening: re-auth gh via keyring so the admin
#              PAT leaves lane envs — separate change.)
#            - CLAUDE_CODE_OAUTH_TOKEN — the lane's Claude account (headless/tmux).
#   OPT-IN — per-lane extras from scripts/lib/lane_env_optin.map (git-tracked),
#            for the few lanes that genuinely need a scoped secret (e.g. a
#            deploy-verify lane -> VERCEL_TOKEN). Default = none.
#
# DROPPED (examples, by omission): the ~15 bot tokens, CONSOLE_DB_URL/CONSOLE_TOKEN,
# SENTRY_AUTH_TOKEN, VPH_VAULT_KEY, SUPABASE_ACCESS_TOKEN, INGEST_DSN,
# MUSA_TELEGRAM_ID, read-only DB URLs, ANTHROPIC_API_KEY (also forces metered
# billing — already unset today). Scripts that still need one read it on-demand
# from .env (e.g. _verify_vercel_deploy greps VERCEL_TOKEN from .env), they do
# NOT depend on it being in the process env.

# Non-secret essentials — generous; none is a credential.
lane_env_safe_names() {
    printf '%s\n' \
        PATH HOME USER LOGNAME SHELL LANG LC_ALL LC_CTYPE LC_MESSAGES TERM TERMINFO \
        TMPDIR TZ COLUMNS LINES \
        TMUX TMUX_PANE \
        CC_AGENT_ID CC_BASE_AGENT_ID SCOPE_REPO \
        ORCH_DIR ORCH_ROOT TMUX_BIN SRE_TMUX_BIN \
        CLAUDE_CODE_SUBAGENT_MODEL CLAUDE_AUTOCOMPACT_PCT_OVERRIDE CLAUDE_CODE_ENTRYPOINT \
        GOOGLE_APPLICATION_CREDENTIALS CLOUDSDK_CONFIG
}

# Minimal SECRETS kept for every lane (verified fleet-wide need).
lane_env_keep_secret_names() {
    printf '%s\n' \
        CLAUDE_CODE_OAUTH_TOKEN \
        DATABASE_URL SUPABASE_DB_URL SUPABASE_URL SUPABASE_SERVICE_KEY \
        ORCHESTRATOR_SUPABASE_URL ORCHESTRATOR_SUPABASE_SERVICE_KEY \
        GH_TOKEN
}

# Per-lane opt-in extras from the git-tracked map. Format: one lane per line,
#   <base_agent_id> <NAME1> <NAME2> ...
# Lines starting with # and blank lines ignored. $1 = base agent id.
lane_env_optin_names() {
    local base="${1:-}" mapf="${ORCH_DIR:-$HOME/wingmen/orchestrator}/scripts/lib/lane_env_optin.map"
    [ -n "$base" ] || return 0
    [ -f "$mapf" ] || return 0
    awk -v b="$base" '!/^[[:space:]]*#/ && NF>=2 && $1==b { for (i=2;i<=NF;i++) print $i }' "$mapf"
}

# The full allow-list of NAMES for a lane (deduped). $1 = base agent id.
lane_env_allow_names() {
    { lane_env_safe_names; lane_env_keep_secret_names; lane_env_optin_names "${1:-}"; } \
        | awk 'NF' | sort -u
}

# Emit, NUL-delimited, `NAME=VALUE` for every allow-listed name CURRENTLY set in
# this process env. Caller feeds these to `env -i` so claude starts from a clean
# env + exactly these. NUL-delimited so values with spaces/newlines are safe.
# $1 = base agent id.
lane_env_scrubbed_pairs0() {
    local base="${1:-}" name val
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if val="$(printenv "$name")"; then
            printf '%s=%s\0' "$name" "$val"
        fi
    done < <(lane_env_allow_names "$base")
}
