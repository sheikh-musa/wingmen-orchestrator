# singleton_model_pin.sh — resolve a singleton body's boot model from its durable pin.
#
# WHY (orch-console #48930, 2026-10-02): boot_fleet_health.sh used
# MODEL="${MODEL:-claude-opus-4-8}" and never read .fleet-health_model, so the
# operator's opus-5-5 pin was inert and a relaunch came up on opus-4-8. A pin
# nothing reads is not a pin. Same class as op#13633 (.quality_model / .cai_model).
#
# Precedence: non-empty MODEL env > pin file (whitespace-stripped, non-empty) > default.
# Echoes "<model>\t<source>" so the caller can log WHICH source won.
# bash-3.2 safe (launchd/tmux /bin/bash); safe under `set -u`.
#
# Usage: IFS=$'\t' read -r MODEL MODEL_SRC < <(resolve_singleton_model <pin_file> <default>)

resolve_singleton_model() {
    local pin_file="$1" default_model="$2" pinned=""
    if [ -n "${MODEL:-}" ]; then
        printf '%s\t%s\n' "$MODEL" "MODEL env"
        return 0
    fi
    if [ -r "$pin_file" ]; then
        pinned="$(tr -d '[:space:]' < "$pin_file" 2>/dev/null || true)"
    fi
    if [ -n "$pinned" ]; then
        printf '%s\t%s\n' "$pinned" "pin $pin_file"
    else
        printf '%s\t%s\n' "$default_model" "default"
    fi
}
