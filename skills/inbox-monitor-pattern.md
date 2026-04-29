# inbox-monitor-pattern

**Source decision:** CAI-PROCESS-INBOX-CADENCE-001 Section B Architecture A (filed 2026-04-29, id 619).

**Audience:** all CC families running interactive sessions, optionally per agent.

**Owner:** cc-orchestrator (skills/ authorship per CAI-AGENTS-002).

## When to invoke

Architecture A is **OPTIONAL per agent** — adopt when in-session reactivity to incoming `agent_messages` is a real need, skip otherwise. The mandatory path is Architecture C (cloud-scheduled inbox sweep) which lives outside the active session and is universal.

Concrete signals that Architecture A pays off for a CC family:

- The session is long-lived (hours of active work) and other agents file P1/P2 messages mid-session that should land within minutes
- The CC is doing focused build work where waiting for the next scheduled-sweep tick (15-240 min depending on priority) introduces real latency
- The operator is in a back-and-forth flow that depends on inter-CC handoff timing (e.g. cc-cosem ships Option C → cc-orchestrator should observe within seconds, not minutes)

If those don't apply, the scheduled-sweep path is sufficient. Adding a Monitor adds tool-call cost + interrupt surface; only do it when the latency arbitrage is real.

## What this skill does

Wires a `Monitor` tool over a `tail -F` of orchestrator log OR a polling SELECT against `agent_messages`, filtering on `to_agent=<my-base-agent-id>` AND `read_at IS NULL`. Each new line / new row becomes a notification mid-session.

## Reference implementation — log tail

When the orchestrator (or some other process) emits structured log lines on inbox state, watch them directly:

```bash
tail -F logs/orch.log 2>&1 | grep -E --line-buffered "agent_messages|new msg #|cc-orchestrator|notify(.+P1|.+P2)"
```

Pros: no DB load, no polling cost, sub-second latency.
Cons: depends on the log emitter actually logging the events (not all paths do); brittle to log format changes.

## Reference implementation — DB poll

When no log surface exists, poll `agent_messages` directly:

```bash
last_id=0
while true; do
  new=$(psql "$DATABASE_URL" -At -c "
    SELECT id || '|' || from_agent || '|' || COALESCE(priority,'P3') || '|' || subject
      FROM agent_messages
     WHERE to_agent = '<my-base-agent-id>'
       AND read_at IS NULL
       AND id > $last_id
     ORDER BY id ASC
  ")
  if [ -n "$new" ]; then
    echo "$new"
    last_id=$(echo "$new" | tail -1 | cut -d'|' -f1)
  fi
  sleep 30
done
```

Pros: works even when no log surface emits the events.
Cons: 30s poll cadence is a hard floor (Supabase pooler discourages tighter intervals); each poll is a round-trip even when idle.

## Section A semantics — what Monitor does NOT do

- **NEVER set `responded_at` from inside the monitor loop.** Section D mandates: scheduled-sweep + monitor are stateless surfaces; only the in-session CC handling the message can set `responded_at`. If the monitor "responds" by, say, auto-acknowledging, that violates Section A — `responded_at` is for substantive dialogue turns, not heartbeat ACKs.
- **NEVER set `read_at` from inside the monitor loop.** `read_at` is the close mechanism for rulings + FYIs (Section A). Setting it from a monitor without actually reading the body would mask unfinished work.
- The monitor's job is **surface only** — emit the event, let the CC's main session decide what to do.

## Cadence + dedup

- Match (or beat) the Section C scheduled-sweep cadence for your priority budget. P1 wants <60min latency; a 30s poll easily meets that.
- Dedup at the CC's end, not inside the monitor — the monitor is allowed to re-emit on every fire; the CC tracks "I've already seen msg #X" via session memory.

## Failure mode

If the monitor process crashes silently (most common — broken pipe on `tail -F`, network blip on `psql`), the CC's in-session reactivity quietly degrades to scheduled-sweep cadence. That's fine — Architecture A is *optional acceleration*, not a load-bearing path. The Section C scheduled-sweep + boot_briefing inbox_sla_violations branch are the structural surfaces that catch missed messages.

## References

- `docs/governance/inbox-check-directive.md` (canonical text for `inbox-check-protocol` skill — load before ANY ping reply or inbox-state report)
- CAI-PROCESS-INBOX-CADENCE-001 (id 619) — Section A field semantics + Section B architecture choice
- CAI-RESP-077 (procedural-invariants-should-be-structural meta-pattern)
- AGENTS.md cold-start protocol step 4
