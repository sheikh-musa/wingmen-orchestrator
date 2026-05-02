# Anthropic API vs Claude CLI — architectural discipline

**Source decisions:** CAI-PROCESS-MAX-FIRST-001 (id 643, ratified 2026-05-02 03:02 SGT), CAI-PROCESS-INBOX-CADENCE-001 (id 619).

**Audience:** all CC families + any operator adding new programmatic Claude calls to wingmen.

**Owner:** cc-orchestrator.

**Discovery context:** Musa paid 3× for Anthropic API auto-recharge credits on 2026-04-30. Initial cc-orchestrator diagnosis was wrong (blamed scheduled-sweep CC spawns, which actually route through Max). The real burn was a tier of `nervous_system/*.py` modules importing `anthropic` directly. The Max-vs-API distinction had never been documented anywhere — every new background task that needed Claude reasoning got `anthropic.Anthropic(api_key=...)` boilerplate as the path of least resistance, accumulating to substantive monthly burn.

This document is the canonical fix. Audit 5 in `nervous_system/orch_self_audit.py` enforces it.

---

## TL;DR

| Path | Billing | Use for |
|---|---|---|
| `claude` CLI subprocess (`claude -p`) | **Max plan subscription** (free at the substrate level subject to rate limits) | Default for all programmatic Claude calls |
| `anthropic.Anthropic(api_key=...)` SDK | **API key auto-recharge credits** (real money per token) | One of 5 specific carve-outs below |
| Anthropic Haiku via SDK | API key, but **cheap** | Auto-pass — no special handling |

**Rule of thumb:** if a new module needs Claude reasoning, default to spawning `claude -p` via subprocess (or use `ai_provider.call_ai` which routes through CLI by default). Reach for the SDK only when one of the 5 carve-outs structurally applies.

---

## The 5 carve-outs (from CAI-PROCESS-MAX-FIRST-001)

A direct-API call site is allowed iff the call falls into ONE of:

1. **`latency_budget_under_3s`** — synchronous user-facing path where p99 latency budget is < 3s. CLI spawn overhead (~1-2s) eats too much of the budget. Note: cto_bot Telegram replies do NOT qualify — Telegram tolerates 5-10s easily. This is for hypothetical web-UI hot paths.

2. **`streaming_structured_output`** — token-by-token streaming response (CLI -p doesn't expose this). Rare; most non-streaming consumers should use CLI.

3. **`vision_multimodal`** — image inputs. CLI surface is text-first. The `_call_claude` function in `ai_provider.py` is the gateway for this case.

4. **Haiku model** (auto-pass via model rule, no comment needed) — `claude-haiku-4-5-*`. Haiku is cheap AND CLI doesn't cleanly route to Haiku (Max plan defaults are Sonnet/Opus). Audit 5 auto-passes any call site whose `model="..."` literal contains `haiku`.

5. **`tool_use_with_caller_defined_tools`** — Anthropic tool-use API with caller-defined tools and caller-side execution (multi-turn `tool_use_id` round-trip). CLI exposes Claude's own tools (Bash, Read, Edit) but cannot inject caller-defined-with-caller-execution tool surfaces. `nervous_system/council_agent.py` is the canonical example.

---

## How to add a direct-API call site (rare)

If you've genuinely hit one of the carve-outs above:

1. Annotate the `anthropic.Anthropic(...)` or `anthropic.AsyncAnthropic(...)` instantiation with a comment:
   ```python
   # llm_route_exempt: <token>
   client = anthropic.Anthropic(api_key=...)
   ```
   where `<token>` is one of:
   - `latency_budget_under_3s`
   - `streaming_structured_output`
   - `vision_multimodal`
   - `tool_use_with_caller_defined_tools`

2. The comment must appear within ±10 lines of the instantiation OR anywhere in the file (Audit 5 does file-level detection).

3. If using Haiku, no comment is needed — Audit 5 auto-passes via the `model="claude-haiku-..."` literal.

4. If you cannot honestly cite one of the 5 carve-outs, **the call site doesn't qualify**. Use `ai_provider.call_ai` (or shell to `claude -p` directly) instead.

5. New carve-outs require a CAI-RESP-* amendment to CAI-PROCESS-MAX-FIRST-001 — they are NOT operator-side or CC-side decisions.

---

## ai_provider.py — the canonical entry point

For most programmatic Claude calls, use `ai_provider.call_ai`:

```python
from ai_provider import call_ai

response = await call_ai("Your prompt here", system="optional system prompt")
```

This routes through CLI by default (Max-covered). Vision (images=) auto-routes to direct API per Carve-Out 3. If you need explicit direct-API for Carve-Outs 1/2/5:

```python
response = await call_ai("...", model="claude_api")
```

**Mitigations baked in (CAI-PROCESS-MAX-FIRST-001 (d)):**
- Concurrency cap of 2 concurrent `claude -p` spawns (configurable via `AI_CLI_CONCURRENCY`)
- Yield mechanism: if `~/.wingmen/cc_active` marker is < 5 min old (interactive session active), background CLI spawns pause
- 30-min hard cap on yield wait so a hung interactive session doesn't deadlock background work

---

## Audit 5 — runtime enforcement

`nervous_system/orch_self_audit.py:_audit_anthropic_sdk_direct_call_sites` walks the repo every 10 min, classifies each `anthropic.Anthropic(...)` instantiation, and fires P2 Telegram alerts on violations. Hour-bucket dedup prevents spam.

Classifications:
- `ok_haiku` — model contains `haiku` (Carve-Out 4)
- `ok_exempt` — has `# llm_route_exempt: <valid_token>` comment
- `violation_no_exempt` — Sonnet/Opus call site, no comment
- `violation_invalid_exempt` — comment present but token not in allowlist

Violations surface in `notification_log` with `source='orch_self_audit.llm_routing_drift'` and `decision_ref='CAI-PROCESS-MAX-FIRST-001'`.

---

## Allowlist — known direct-API call sites (as of 2026-05-03)

| File:line | Reason | Token |
|---|---|---|
| `ai_provider.py:154` (`_call_claude`) | Vision gateway + carve-out opt-in entry point | `vision_multimodal` |
| `nervous_system/council_agent.py:356` | Caller-defined tool-use loop (5 tools) | `tool_use_with_caller_defined_tools` |
| `ralph_runner.py:169` | Haiku alignment-verify step | (auto-pass via Haiku model rule) |
| `nervous_system/ecosystem_auditor.py:393` | Haiku auditor calls | (auto-pass) |
| `nervous_system/wingmen_dream.py:69` | Haiku dream synthesis | (auto-pass) |

`projects/dookana/backend/app/services/generator_service.py` lives outside the orchestrator repo; its routing discipline is dookana's call (and is currently DOOKANA-FREEZE-001 scoped).

---

## Why this matters (short version)

Treating every Claude call as "free because we have Claude" papered over a real cost surface. Max-covered CLI usage IS subject to rate limits, but those are the same shared pool you (Musa) already paid for. SDK calls bill auto-recharge credits separately.

The principle: **don't reach for paid compute when prepaid compute is sitting idle.** It's computational israaf.

---

## References

- CAI-PROCESS-MAX-FIRST-001 (id 643) — full decision text
- CAI-PROCESS-INBOX-CADENCE-001 (id 619) — Section A semantics this extends
- CAI-RESP-107 / CAI-RESP-108 — discovery + ratification thread
- `nervous_system/orch_self_audit.py:_audit_anthropic_sdk_direct_call_sites` — audit code
- `ai_provider.py:_call_cli_route` — CLI route implementation
- `skills/feedback_max_plan_first.md` — cc-orchestrator-side memory rule
- CAI-STAFF-SPEC-001 §5.2 — yield mechanism convergence point with wingmen-staff
