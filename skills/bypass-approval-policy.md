# bypass-approval-policy

**Source decision:** CAI-PIPELINE-BYPASS-001 (filed 2026-04-23, Option (b) ruling).

**Audience:** all CC families running autonomous-fix pipelines. Consumed via transclusion (markdown include / canonical reference) by `cc-cosem`, `cc-scholar`, `cc-orchestrator`, future families.

**Owner:** cc-orchestrator (skills/ directory authorship per CAI-AGENTS-002).

## When this applies

When a CC family's autonomous-fix pipeline encounters a structural gap that prevents normal flow (e.g., REPOS.json missing a repo entry, dispatcher crash, PR-flow failure), AND the CC has a substantive understanding of the diagnosis + a fix preview ready.

## What "bypass" means

Direct application of the fix outside the orchestrator's pipeline (manual git commit-and-push, manual deploy trigger, manual `bug_reports.status='deployed'` write).

## Procedure

1. **CC files diagnosis + pipeline-gap report + bypass-approval-request** in agent_messages addressed to `musa` (priority='P1', requires_response=true). Body MUST follow this template:

   ```
   ## Bug context
   - bug_id: <uuid>
   - repo_name: <name>
   - status before bypass: <pr_open / proposed / approved / etc>

   ## Diagnosis
   <root cause + grounding code/log refs — file:line format>

   ## Pipeline gap
   <which orchestrator step failed and why; specific exception or
   condition that prevented the normal flow>

   ## Fix preview
   <what the bypass will do — specific git commits, deploy commands,
   manual DB writes — verbatim>

   ## Target CC ack
   <agent_id of the CC that will execute the bypass; this CC posts
   a separate AGREED reply on the same thread BEFORE Musa approval
   to confirm they understand the fix preview and own its execution>

   ## Risk window
   <if emergency override path: estimated time-to-incident-if-not-bypassed,
   typically <1hr like a user-facing crash blocking onboarding>
   ```

2. **Musa approval is captured per-incident** (bug_id, reason, fix preview, target CC ack). Approval is explicit; no implicit/silent consent.

3. **No strategic_decisions row required per-incident.** Bypass is captured in `bug_reports.manual_override_reason` (≥20 chars trimmed) at the time of the manual `status='deployed'` write. Schema CHECK enforces the constraint.

4. **Emergency override** (Musa unreachable, fix is <1hr risk): post-hoc P1 notification to Musa + cai within 15 minutes of the manual write. Same `manual_override_reason` discipline applies.

## Emergency override examples

**Qualifies (<1hr risk):**
- User-facing onboarding flow crashes affecting a known active client (e.g., cc-scholar bug 2386d2a4 — report-bug button blocking playback during a Surah-completion session).
- Production data corruption that compounds with each request (e.g., a bad migration that's mis-stamping fee rows).
- Security exposure that's already in the wild (leaked secret, RLS bypass).

**Does NOT qualify:**
- Test suite failure not blocking a deploy.
- Internal-only dashboard glitch.
- "I want to ship the fix faster than the review window" — that's the perverse-incentive case CAI-PIPELINE-BYPASS-001 explicitly rejected.
- Recurring failure pattern — recurrence means the pipeline needs structural fix, not bypass.

## What this is NOT

- NOT a license for routine pipeline-gap workarounds. Recurring bypasses are evidence of a structural issue requiring a fix to the pipeline itself.
- NOT a path to silent deploy. Every bypass leaves a `manual_override_reason` audit trail visible in `boot_briefing.manual_override_bugs`.
- NOT a replacement for ORCHESTRATOR-STATUS-001 Option B verification. Bypassed rows skip Option B's verifier (which checks `manual_override_reason IS NULL` in its predicate). The bypass is the structural acknowledgment that this row was NOT mechanically verified.

## Boot_briefing surface

`boot_briefing.manual_override_bugs` shows recent overrides with bug_id, repo, reason prefix. Operators review weekly or on incident.

## References

- CAI-PIPELINE-BYPASS-001 (parent decision)
- ORCHESTRATOR-STATUS-001 Option B (verifier predicate excludes manual_override_reason IS NOT NULL rows)
- bug_reports schema: `manual_override_reason TEXT NULL` + CHECK constraint
- cc-scholar bug 2386d2a4 (first retroactive approval, set the precedent)
