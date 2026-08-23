# auditor_lanes.sh — SSOT for the FULL-tier auditor lanes (CAI-RESP-1170).
#
# cc-quality + cc-storefront render governance and MUST run on claude-opus-4-8 —
# never a Sonnet cost-flip. This list is the SINGLE source of that fact, sourced by
# BOTH:
#   * scripts/fleet_model.sh          — the --live flip carve-out (skip a non-opus flip)
#   * scripts/lib/model_precedence.sh — the LAUNCH-cascade clamp (force opus at launch)
# so the carve-out can NEVER be enforced in one path but not the other. That split is
# the exact gap this closes: the list + carve-out lived only in fleet_model.sh, so a
# fresh auditor launch with no .<session>_model pin + a Sonnet .fleet_model resolved to
# Sonnet through model_precedence.sh, silently violating CAI-1170 (cc-storefront did).
#
# Overridable via the environment (for tests); defaults to the two live auditors. Add a
# new FULL auditor HERE — one edit reaches both the flip tool and the launch path.
AUDITOR_LANES="${AUDITOR_LANES:-quality storefront}"

# is_auditor_lane <session> — return 0 iff <session> is a FULL auditor lane, else 1.
# THE shared matcher (cc-quality #32146 nit-1): both fleet_model.sh and
# model_precedence.sh call this, so the LIST and the MATCH logic are both SSOT — a
# second inline grep would re-open the same two-copies drift one level down.
# Tolerates the cc- identity prefix (cc-quality) as well as the bare tmux session name
# (quality), since callers pass either form. An empty session is never an auditor.
# grep -F: match $s as a LITERAL, never a regex (#32146 nit-2).
# FORWARD-GUARD (#32146 nit-3): the -w match is word-boundaried, so a FUTURE
# multi-instance auditor (e.g. cc-quality-1 -> 'quality-1') would MISS and launch
# UN-clamped. No live gap today (the two auditors are singletons); make this
# suffix-tolerant (strip a trailing -<N>) BEFORE any auditor goes multi-instance.
is_auditor_lane() {
    local s="${1:-}"
    s="${s#cc-}"
    [ -n "$s" ] && printf '%s ' $AUDITOR_LANES | grep -Fqw -- "$s"
}
