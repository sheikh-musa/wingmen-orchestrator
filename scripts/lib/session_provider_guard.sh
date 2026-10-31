#!/usr/bin/env bash
# session_provider_guard.sh — shell shim for the cross-provider resume guard
# (orch-console bus #87276, P0 Musa op 36795). Logic + tests live in
# scripts/lib/session_provider_guard.py and tests/test_session_provider_guard.py;
# this file only locates the interpreter and shapes the answer for bash callers.
#
# WHY A SHIM: every named boot path (launch_dangerous_cc.sh <- launch_lane_as.sh,
# switch_lane_token.sh; boot_orch.sh --continue; boot_mirror.sh --resume;
# boot_nazim.sh passthrough) is bash. Putting the check in ONE function means the
# named scripts cannot drift from each other, and a new launcher gets the guard
# by calling it, not by remembering to.
#
# Behaviour contract:
#   spg_check <model> --session <id>          -> 0 allow / 3 refuse
#   spg_check <model> --continue --cwd <dir>  -> 0 allow / 3 refuse
#   On refusal, the handoff note path is printed on a HANDOFF line.
#   Any other rc (1, 2) is an ERROR and must be treated as a refusal by callers.
#
#   spg_strip_resume_flags <arrayname>  -> removes --resume <id> / -r <id> /
#                                          --continue / -c from that array,
#                                          so the lane boots FRESH instead of
#                                          replaying a transcript across
#                                          providers (the documented default).

SPG_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPG_ORCH_DIR="$(cd "$SPG_LIB_DIR/../.." && pwd)"

spg_python() {
    if [ -x "$SPG_ORCH_DIR/.venv/bin/python3" ]; then
        printf '%s' "$SPG_ORCH_DIR/.venv/bin/python3"
    else
        printf '%s' "python3"
    fi
}

# spg_check <model> <guard args...>
spg_check() {
    local model="$1"; shift
    # SPG_LOG_DIR is overridable so a test (or a one-off dry run) cannot scatter
    # handoff notes into the live checkout's logs/.
    "$(spg_python)" "$SPG_LIB_DIR/session_provider_guard.py" check \
        --model "$model" \
        --log-dir "${SPG_LOG_DIR:-$SPG_ORCH_DIR/logs/provider-switch-guard}" \
        "$@"
}

# spg_strip_resume_flags CLAUDE_PASSTHROUGH
spg_strip_resume_flags() {
    local name="$1"
    local -a src=() out=()
    eval "src=(\"\${$name[@]+\"\${$name[@]}\"}\")"
    local i=0
    while [ "$i" -lt "${#src[@]}" ]; do
        case "${src[$i]}" in
            --resume|-r)
                # drop the flag AND its session id (a bare --resume opens the
                # interactive picker — equally a cross-provider resume)
                i=$((i + 2))
                ;;
            --resume=*)
                i=$((i + 1))
                ;;
            --continue|-c)
                i=$((i + 1))
                ;;
            *)
                out+=("${src[$i]}")
                i=$((i + 1))
                ;;
        esac
    done
    eval "$name=(\"\${out[@]+\"\${out[@]}\"}\")"
}
