# inbox-check-protocol

**Source decision:** ORCHESTRATOR-NOTIFIER-FIX-001-AMEND Fix 4 (filed 2026-04-23, ownership cc-orchestrator post-AGENTS-002).

**Audience:** all CC families running interactive sessions.

**Owner:** cc-orchestrator (skills/ authorship per CAI-AGENTS-002).

## When to invoke

Invoke this skill before ANY of the following:

- Replying to `ping`
- Replying to "inbox clean", "what's pending", "any new messages", "anything to triage", "check messages"
- Reporting on session-end status that includes inbox state
- ANY response where the question is "what does my inbox currently contain"

If you are about to type "pong" or "inbox clean" without first executing a fresh `agent_messages` SELECT, **stop and invoke this skill instead.**

## What this skill does

Points at the canonical directive at `docs/governance/inbox-check-directive.md`.

The directive specifies:
1. Mandatory fresh-SELECT pattern (no session-cache reports)
2. Cross-check query shape (catches PostgREST stale-read drift — observed twice this project)
3. Boot-time inbox query (cold-start protocol step 4)
4. `read_at = now()` write on processing (feeds Fix 5 unread-backlog audit)
5. Violation acknowledgement pattern (no defense, audit-of-violations trail)

Read the directive in full before applying. The query shape is non-trivial (two-filter cross-check with stale-read detection); copy it verbatim until you've internalized why both filters are needed.

## Failure mode (what this prevents)

Reporting `inbox clean` from session cache while messages sit unread in the database is structurally indistinguishable from lying. The shura loop depends on inbox visibility being honest; this skill is the discipline gate that keeps that property structural.

Prior incidents motivating the discipline:
- cc-ihsanos msg #573 (2026-04-22) — "pong — inbox clean" reported from cache while 9 messages queued. Acknowledged without defense in msg #575.
- cc-orchestrator (2026-04-25, 2026-04-27) — `.eq()` filter chain returned stale data; missed messages caught only after Musa prompted "check again". Cross-check pattern in the directive's query shape closes this gap.

## References

- `docs/governance/inbox-check-directive.md` (canonical text — read this for the actual procedure)
- ORCHESTRATOR-NOTIFIER-FIX-001-AMEND (parent decision, Fix 4)
- CAI-RESP-077 (procedural-invariants-should-be-structural meta-pattern)
- AGENTS.md cold-start protocol step 4
