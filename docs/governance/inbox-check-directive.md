# Canonical Inbox-Check Directive

**Filed under:** CAI-RESP-075 (N1) → ORCHESTRATOR-NOTIFIER-FIX-001-AMEND Fix 4a
**Applies to:** all CC agents (cc-ihsanos, cc-scholar, cc-cosem, cc-orchestrator)
**Authority:** Wingmen governance protocol; violations are integrity failures

## The rule

Before responding to any inbox-status query — "pong", "what's pending", "any new messages", "check inbox", or any semantic variant — execute a fresh `SELECT` against `agent_messages`:

```sql
SELECT id, from_agent, subject, requires_response, created_at, priority
  FROM agent_messages
 WHERE to_agent = '<my_agent_id>'
   AND read_at IS NULL
 ORDER BY created_at DESC;
```

Do not report from session context. Session-cached inbox state is considered stale after any tool-use interaction or user-turn boundary.

## Why

Stale inbox reports mask the actual state of the governance bus. When cc-ihsanos reports "inbox clean" while 9 messages sit queued, humans cannot trust status reports — and the shura loop breaks at the telemetry layer.

Evidence: on 2026-04-22 in session msg #573, cc-ihsanos violated this rule and answered "pong — inbox clean" from session cache while 9 queued messages (ids 552-560) sat unread. The violation was caught by Musa asking "check again" which forced a fresh query. The amendment ORCHESTRATOR-NOTIFIER-FIX-001-AMEND was filed specifically to codify this rule.

## Operational mechanics

1. **Every turn — not just session start.** ihsanos CLAUDE.md §5 already mandates session-start inbox check; this directive tightens to per-turn for inbox-status prompts specifically.

2. **Write `read_at` when you process.** When you process an inbox message (respond, defer, or triage), `UPDATE agent_messages SET read_at = now() WHERE id = <id>`. Not optional. This feeds Fix 5's `unread_backlog` observability.

3. **Acknowledge violations without defense.** If a reader catches you reporting from session cache, the correct response is verbatim acknowledgment: "Confirmed violation. No defense." Not "but I thought the inbox was clean" or "my cache was current." Defense signals governance drift worth escalating.

## Applies to

- cc-ihsanos (ihsanos repo + wingmen-orchestrator platform scope)
- cc-scholar (ai-scholar, hifz-companion repos)
- cc-cosem (cosem-tdu, cosem-adcda repos)
- cc-orchestrator (wingmen-orchestrator internal agent, if distinct from cc-ihsanos)

Each CC family's CLAUDE.md references this file via transclusion:

```markdown
## §5 Inbox check
See canonical directive: [inbox-check-directive.md](<path-to-orchestrator>/docs/governance/inbox-check-directive.md)
```

Path handling is per-family — symlinks, relative paths, or absolute paths all acceptable. Verbatim copy is discouraged (drift risk).

## Audit trail

Violations acknowledged without defense are recorded in `session_digests` or `agent_messages` with `message_type='violation_ack'` (if/when that message_type exists; otherwise as an `update` with clear subject line).

First recorded violation: cc-ihsanos, session 2026-04-22/23, on "pong" response. Acknowledged in msg #575.
