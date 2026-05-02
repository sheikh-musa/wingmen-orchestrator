# api-vs-cli-routing

**Source decision:** CAI-PROCESS-MAX-FIRST-001 (filed 2026-05-01, id 643).

**Audience:** all CC families + operators adding new programmatic Claude calls anywhere in the wingmen substrate.

**Owner:** cc-orchestrator (skills/ authorship per CAI-AGENTS-002).

## When to invoke

Invoke this skill BEFORE any of:

- Adding a new module/file that calls `anthropic.Anthropic(...)` or `anthropic.AsyncAnthropic(...)` directly
- Modifying an existing direct-API call site
- Designing a new background task that needs Claude reasoning
- Investigating Anthropic API auto-recharge burn

## What this skill does

Points at the canonical directive at `docs/governance/api-vs-cli-architecture.md`.

The directive specifies:

1. The **default rule:** spawn `claude -p` via subprocess (Max-covered) for all programmatic Claude calls. Use `ai_provider.call_ai` which handles this.
2. The **5 carve-outs** for direct API: `latency_budget_under_3s`, `streaming_structured_output`, `vision_multimodal`, Haiku model (auto-pass), `tool_use_with_caller_defined_tools`.
3. The **annotation discipline:** carve-out call sites need `# llm_route_exempt: <token>` comments matching the allowlist.
4. **Audit 5** (`orch_self_audit._audit_anthropic_sdk_direct_call_sites`) enforces this at runtime — fires P2 alerts on drift.
5. **Mitigation layers:** concurrency cap (2 concurrent CLI spawns), yield mechanism (background pauses while interactive session active), no silent fallback.

Read the directive in full before adding ANY direct-API call site. The audit will fire if you don't, but catching it before commit is cleaner than fixing it after.

## Failure mode (what this prevents)

The 2026-04-30 incident: Musa paid 3× for Anthropic API auto-recharge credits in a short window. Initial diagnosis was wrong (blamed CLI-route Phase 3 sweep, which is Max-covered). Real burn was a tier of `nervous_system/*.py` modules with direct API calls that had accumulated organically because the Max-vs-API distinction had never been documented anywhere.

This skill is the discipline gate that keeps prepaid compute used and paid compute reserved for the cases that structurally need it.

## References

- `docs/governance/api-vs-cli-architecture.md` (canonical text — read this for the actual procedure)
- CAI-PROCESS-MAX-FIRST-001 (id 643) — parent decision
- CAI-PROCESS-INBOX-CADENCE-001 (id 619) — Section A semantics this extends
- `nervous_system/orch_self_audit.py:_audit_anthropic_sdk_direct_call_sites` — audit code
- `ai_provider.py:_call_cli_route` — CLI route implementation
