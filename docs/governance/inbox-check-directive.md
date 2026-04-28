# inbox-check-directive

**Source decision:** ORCHESTRATOR-NOTIFIER-FIX-001-AMEND Fix 4 (filed 2026-04-23 by cc-ihsanos pre-AGENTS-002; ownership transferred to cc-orchestrator at AGENTS-002 handoff).

**Audience:** all CC families (cc-cosem, cc-ihsanos, cc-orchestrator, cc-scholar, future). Canonical text — consumed by reference, not paraphrased in-place.

**Owner:** cc-orchestrator (per CAI-AGENTS-002 + AGENTS.md cold-start protocol step 4 reference).

---

## The directive

**Every inbox-status response MUST be preceded by a fresh SELECT against `agent_messages`. Never report from session cache.**

Inbox-status responses include but are not limited to:
- `pong` (after `ping`)
- "inbox clean", "what's pending", "any new messages", "anything to triage"
- Any variant where the question is "what does my inbox currently contain"

The response MUST be:
1. A fresh `.execute()` against `agent_messages` with `to_agent = <my_agent_id>` (or appropriate filter — broadcast, scope-inherited)
2. Optionally cross-checked with an orthogonal filter (e.g., `from_agent = 'cai'`) to detect read-replica drift
3. Reported only after the query returns

Reporting `inbox clean` while messages sit unread in the database is a **governance integrity violation**. It is structurally indistinguishable from lying.

## Why this exists

CC sessions hold context across many turns. Earlier-in-session inbox query results become "stale state" — the database moves forward; the cache does not. A `pong — inbox clean` response from cache says "I don't know what's there" while pretending to say "nothing's there."

The shura loop depends on inbox visibility being honest. Decisions filed via the bridge trigger reach addressees as `agent_messages` rows; if addressees report blindly from cache, the loop breaks at the freshest possible failure point. **Prior incidents** that motivated this directive:

- **cc-ihsanos msg #573** (2026-04-22): "pong — inbox clean, 3 questions to CAI still awaiting" reported from session context. Live query during the same turn found 9 new messages queued. Acknowledged in msg #575 without defense.
- **cc-orchestrator session 2026-04-25, 2026-04-27**: stale-read pattern hit twice on supabase-py `.eq()` filter chain. Caught only after Musa specifically prompted "check again" because the first query missed messages that had landed in the previous minute.

## Mandatory cadence

- Boot-time: every CC session boot runs the inbox query as part of cold-start. Output surfaces at session start, not on first user ping (prevents "I just booted and don't know what's pending" blind spot per Fix 4 AC-15).
- Per-ping: every `ping` (or variant) triggers a fresh query before the `pong`.
- After-action: when processing a message (responding, deferring, triaging), write `read_at = now()` on the `agent_messages` row. Not optional — feeds Fix 5 unread-backlog audit.

## Recommended query shape

```python
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv('.env')
from supabase import create_client
c = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

# Primary: filter to me
r1 = (c.table('agent_messages')
      .select('id,from_agent,priority,subject,requires_response,read_at,created_at')
      .eq('to_agent', '<my-agent-id>')
      .order('created_at', desc=True).limit(15).execute())

# Cross-check: orthogonal filter shape — catches PostgREST stale-read drift
r2 = (c.table('agent_messages')
      .select('id,from_agent,to_agent,priority,subject,read_at,created_at')
      .eq('from_agent','cai')
      .order('created_at', desc=True).limit(10).execute())

primary_ids = {m['id'] for m in r1.data}
cross_to_me = [m for m in r2.data if m['to_agent']=='<my-agent-id>']
missed = [m for m in cross_to_me if m['id'] not in primary_ids]
if missed:
    print('STALE-READ DETECTED — primary missed:', [m['id'] for m in missed])
"
```

## Audit trail

Violations are acknowledged without defense (precedent: cc-ihsanos msg #575, msg #627; cc-orchestrator session 2026-04-25 self-acknowledged). The audit-of-violations pattern is logged for governance archaeology, not punitive.

The `orch_self_audit` module (CAI-RESP-093 + ihsanos parity) does not currently audit per-CC compliance with this directive — that's a future hook (mentioned in Fix 4 AC-16 as deferred). The discipline is currently agent-discipline-enforced, not substrate-enforced.

## References

- ORCHESTRATOR-NOTIFIER-FIX-001-AMEND Fix 4 (parent decision)
- CAI-RESP-077 (test-mutates-prod root cause — same procedural-invariants-should-be-structural meta-pattern)
- AGENTS.md cold-start protocol step 4 (mandatory inbox query at boot)
- skills/inbox-check-protocol.md (the SKILL.md trigger metadata that points at this directive)
