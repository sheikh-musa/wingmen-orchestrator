---
name: inbox-check
description: Use BEFORE any inbox-status response — "pong", "what's pending", "any new messages", "check inbox" or any semantic variant. Executes a fresh SELECT against agent_messages instead of reporting from session cache. Do NOT skip for "I just checked a moment ago" — session cache is considered stale after any tool-use or user-turn boundary. Applies to all CC agents (cc-ihsanos, cc-scholar, cc-cosem, cc-orchestrator). Canonical rule: docs/governance/inbox-check-directive.md.
---

# Inbox Check

## When to invoke

User says anything like: "ping", "pong", "what's pending", "any messages", "check inbox", "status", "new mail", "what came in". Any inbox-status prompt triggers this skill.

## What to do

1. Run the fresh SELECT:

```sql
SELECT id, from_agent, subject, requires_response, priority, created_at
  FROM agent_messages
 WHERE to_agent = '<my_agent_id>'
   AND read_at IS NULL
 ORDER BY created_at DESC;
```

Execute via the appropriate client (orchestrator Supabase REST, `psycopg`, or Supabase MCP — whichever is conventional in the current session).

2. Report the ACTUAL result. If the result is empty, say "inbox clean" — that's now grounded. If the result has rows, list them.

3. If you process any inbox messages this turn, UPDATE their `read_at`:

```sql
UPDATE agent_messages SET read_at = now() WHERE id = <id>;
```

## When NOT to skip

- "I just queried 30 seconds ago" — still stale. Tools may have fired between prompts. Re-query.
- "The user is impatient" — the directive exists because stale reports are worse than 500ms latency.
- "I know the inbox is clean because I cleared it" — re-query. Something may have arrived.

## Applies to

All CC agents. This skill ships as infrastructure shared across the /skills/ autoload tree.

## Canonical rule

See [docs/governance/inbox-check-directive.md](../../docs/governance/inbox-check-directive.md) for full context, including rationale, violation handling, and audit trail expectations.
