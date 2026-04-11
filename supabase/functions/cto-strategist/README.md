# cto-strategist Edge Function

The "second mind" in every strategic discussion inside the Wingmen CTO Council. Triggered by a Postgres trigger on `cto_council` INSERT where `role='claude_code'`. Reads the thread + `wingmen_brain` snapshot + the latest `claude_code` row's `context` field, calls the Anthropic API with an adversarial system prompt, writes a `claude_ai` response row back.

## Role in the architecture

One table (`cto_council`), two AI voices, one human ruler:

```
Musa (Telegram)
  → Wingmen Orchestrator writes musa row
Claude Code (terminal)
  → council.py helper writes claude_code row (with repo context)
    → Postgres trigger fires
      → pg_net.http_post → this Edge Function
        → reads thread + wingmen_brain + claude_code.context
          → calls Anthropic API with CTO_STRATEGIST_PROMPT.md
            → writes claude_ai row back
              → (no trigger — loop prevention)
Claude Code reads the claude_ai row, pushes back or concurs
  → loop until consensus / escalation / circuit breaker
```

Consensus requires at least one prior `[PUSHBACK]` tag from the strategist — this prevents first-round rubber-stamping, which would defeat the whole point of having a second mind.

## Files

- `index.ts` — the function entry point
- `cto_strategist_prompt.md` — **the bundled system prompt**. This is the most important file in the directory. It encodes Musa's decisional framework (adversarial posture, Islamic constraints, CTO_PRINCIPLES.md references, context-grounding rules).
- `deno.json` — import map + fmt config
- `README.md` — this file

## Environment variables

Read by the function at runtime (set via Supabase Function secrets):

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Anthropic API key for Claude calls |
| `STRATEGIST_MODEL` | `claude-haiku-4-5-20251001` | Model ID. Swap to Sonnet/Opus via env flip |
| `STRATEGIST_MAX_TOKENS` | `2000` | Max output tokens per strategist turn |
| `STRATEGIST_SESSION_TOKEN_HARD_CAP` | `60000` | Circuit breaker: halt session above this total token count |
| `STRATEGIST_SESSION_USD_HARD_CAP` | `5.00` | Circuit breaker: halt session above this cumulative USD spend |
| `SUPABASE_URL` | (auto-injected) | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | (auto-injected) | Service-role JWT for RLS bypass |

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are automatically provided by the Supabase Edge Functions runtime — no action needed.

## Deployment

```bash
# From the orchestrator repo root
supabase functions deploy cto-strategist --project-ref tscuymavysscrvoberrr
```

After deploy, set the required settings on the database so the trigger can find the function:

```sql
-- Run once per environment, or after rotating the service role key
ALTER DATABASE postgres SET app.cto_strategist_url = 'https://tscuymavysscrvoberrr.supabase.co/functions/v1/cto-strategist';
ALTER DATABASE postgres SET app.cto_strategist_jwt = '<service-role-JWT>';
```

Set the function-side secrets:

```bash
supabase secrets set \
  ANTHROPIC_API_KEY=sk-ant-... \
  STRATEGIST_MODEL=claude-haiku-4-5-20251001 \
  --project-ref tscuymavysscrvoberrr
```

## Health check

```bash
curl "https://tscuymavysscrvoberrr.supabase.co/functions/v1/cto-strategist?mode=health" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY"
# → {"ok":true,"model":"claude-haiku-4-5-20251001","ts":"..."}
```

## Manual invocation (testing)

```bash
curl -X POST "https://tscuymavysscrvoberrr.supabase.co/functions/v1/cto-strategist" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_id": 1}'
```

This bypasses the Postgres trigger and directly invokes the function for session 1. Useful for:

- Re-running a stuck turn after fixing a config issue
- Testing prompt changes without waiting for a new claude_code insert
- Smoke-testing deploy success

## Cost caching strategy

The function uses Anthropic prompt caching aggressively to keep per-turn cost near ~$0.017 on Haiku 4.5:

| System block | Caching | Why |
|---|---|---|
| `cto_strategist_prompt.md` | `ephemeral` | Stable for the entire session — read cost at 10% |
| `wingmen_brain` snapshot | `ephemeral` | Refreshed every ~4h by the brain_sync cron. Within a 5-min cache window it's fixed |
| Session meta + `claude_code.context` | **none** | Changes every round — caching would waste cache reads |

Messages (thread history) are passed uncached — they grow every round and cache invalidation is complex. Projected cost: ~$0.017 per turn with 75% of input hitting the cache path. Full 6-round deliberation ≈ $0.10.

## Anti-staleness architecture

This function is the load-bearing piece of the anti-staleness contract documented in migration `20260412000001_cto_council.sql`. On every invocation it rebuilds a fresh view of:

1. **Static layer** (`cto_strategist_prompt.md`) — hard constraints only, loaded at cold start
2. **Ecosystem layer** (`wingmen_brain`) — queried live, staleness warning injected if >4h
3. **Repo layer** (`claude_code.context`) — read from the triggering row, staleness warning injected if thin
4. **Thread layer** (live `cto_council` reads) — zero-latency, always fresh

If the claude_code context is thin (no repo name, no commits, no STATUS.md), the function injects a `[INSUFFICIENT_CONTEXT]` directive into the system prompt so the strategist asks for more context rather than speculating. This is a hard architectural invariant — don't remove it.

## Debugging

All function execution is logged to the Supabase Edge Functions log tail:

```bash
supabase functions logs cto-strategist --project-ref tscuymavysscrvoberrr --tail
```

Look for:

- `[cto-strategist] Anthropic API 4xx/5xx` — upstream API errors
- `strategist tagged CONCUR without prior PUSHBACK` — first-round rubber-stamp attempt (ignored by the function but noteworthy — may indicate system prompt tuning needed)
- `session X already ended` — trigger fired on a row after session was closed; typically harmless

## Failure modes and their handling

| Failure | Handling |
|---|---|
| Anthropic API error | Write a `system` row with `STRATEGIST_ERROR` tag, return 502. Musa can manually resume or escalate from the thread. |
| Session already ended | Return early with `skipped: session_ended`. No work done. |
| Thin context field | Inject warning into system prompt. Strategist is instructed to tag `[INSUFFICIENT_CONTEXT]` and ask for facts. |
| Brain snapshot stale (>8h) | Inject `[UNVERIFIED — brain stale]` warning. Strategist prefixes any ecosystem-level claim with the warning. |
| Token hard cap hit | End session with reason `circuit_breaker_tokens`. |
| USD hard cap hit | End session with reason `circuit_breaker_usd`. |
| Max rounds reached | End session with reason `max_rounds`. Orchestrator should post escalation summary to Telegram. |

## Related files

- Migration: `../migrations/20260412000001_cto_council.sql`
- Orchestrator-side helper (Python): `~/wingmen/orchestrator/scripts/council.py` (companion, see that file for Claude Code's side of the flow)
- System prompt: `./cto_strategist_prompt.md`
