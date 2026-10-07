# auditor_lanes.sh — SSOT for the FULL-tier (opus-4-8-clamped) auditor lanes
# (CAI-RESP-1170, narrowed by CAI-RESP-1440, re-pointed by op#27235/#58436).
#
# The FULL-tier auditor renders governance verdicts and MUST run on claude-opus-4-8 —
# never a Sonnet cost-flip. This list is the SINGLE source of that fact, sourced by BOTH:
#   * scripts/fleet_model.sh          — the --live flip carve-out (skip a non-opus flip)
#   * scripts/lib/model_precedence.sh — the LAUNCH-cascade clamp (force opus at launch)
# so the carve-out can NEVER be enforced in one path but not the other. That split is
# the exact gap this closes: the list + carve-out lived only in fleet_model.sh, so a
# fresh auditor launch with no .<session>_model pin + a Sonnet .fleet_model resolved to
# Sonnet through model_precedence.sh, silently violating CAI-1170.
#
# History of WHO holds the clamp (one lane at a time — this list stays the SSOT):
#   * CAI-RESP-1170 — cc-quality + cc-storefront both FULL (opus-4-8).
#   * CAI-RESP-1440 (2026-10-01, Musa op#24365) — narrowed to cc-storefront only;
#     cc-quality moved onto its own `.quality_model` sonnet-5 pin, unclamped.
#   * op#27235 / #58436 (2026-10-07, Musa; CAI-RESP-1440 confirm role re-pointed) —
#     the FULL-tier confirm role moves cc-storefront -> cc-quality. cc-quality is now
#     the SOLE opus-4-8 FULL auditor; cc-storefront drops OFF the clamp and resolves
#     through the normal cascade (.fleet_model = claude-sonnet-5). This unblocks
#     cc-storefront, which was stuck on opus-4-8 purely by this clamp.
#
# Overridable via the environment (for tests); defaults to the one live FULL auditor.
# Add a new FULL auditor HERE — one edit reaches both the flip tool and the launch path.
AUDITOR_LANES="${AUDITOR_LANES:-quality}"

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
