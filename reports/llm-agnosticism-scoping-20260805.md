# LLM Provider-Agnosticism — Scoping Plan (Internal Lanes)

_2026-08-05 · scoping only, no code/config changes · target = INTERNAL non-client-data lanes first (op#10366/10369)._

**Verdict: worth a scoped PILOT, not a fleet-wide abstraction yet.** Savings are real but only on the right task class.

## Mechanism (best → worst fidelity)
1. **Native Anthropic-compatible endpoint (no shim) — PREFERRED.** DeepSeek (`ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`) and Z.ai/GLM (`https://api.z.ai/api/anthropic`) expose a real Anthropic Messages surface — Claude Code switches with ONE env var, no translation layer in the tool-call path. Lowest blast radius.
2. **claude-code-router (`@musistudio/claude-code-router`)** — mature local proxy, per-request-type routing (cheap model for compact/summarize, strong model for edits). Launch lanes with `ccr code`.
3. **LiteLLM proxy** — Anthropic-in → OpenAI/DeepSeek-out; central gateway with keys/spend-tracking/failover. Best when scaling many lanes centrally.

**The crux caveat:** Claude Code's tool schemas are tuned for Claude. Consistent finding: simple tool calls work on alternatives; **long/complex tool-call chains degrade**. Fidelity order: native Claude > native Anthropic-compat (DeepSeek/GLM) > OpenAI-compat via translating proxy > local (Ollama).

## Provider matrix (per 1M tok, USD, Aug 2026, ±20% directional — re-verify before committing)
| Model | In | Out | Compat | Agentic quality | Notes |
|---|---|---|---|---|---|
| Anthropic Opus 4.8 (baseline) | ~5.00 | ~25.00 | native | best-in-class | the bar |
| Anthropic Sonnet 5 | ~3.00 (2 intro) | ~15.00 (10 intro) | native | very strong | the "cheaper Claude, zero risk" option |
| **DeepSeek V4-Flash** | ~0.14 | low | **Anthropic-native** + OpenAI | decent, cheapest credible | ~35x cheaper input than Opus; **best first PoC pick** |
| DeepSeek V4-Pro | ~0.44 | ~0.87 | Anthropic-native + OpenAI | stronger; ~80% SWE-bench (soft) | deep discount tier |
| Qwen3-Coder-Next | ~0.11 | ~0.80 | OpenAI only | ~70% SWE-bench | cheapest coding API; needs proxy → fidelity risk |
| **GLM (Z.ai Coding Plan)** | **flat $10–80/mo** | — | **Anthropic-native** | strong open-weight (~62% SWE-bench Pro) | flat-rate can beat per-token for always-on lanes |
| Kimi K2.6/K2.7-Code | ~0.60–0.95 | ~3–4 | OpenAI only | strongest agentic story (~80%, soft) | priciest alt; needs proxy |

Drop-in (Anthropic-native): DeepSeek, GLM. Need LiteLLM/CCR shim: Qwen, Kimi.

## Cost-per-COMPLETED-task (not sticker)
Sticker = 30–80x cheaper. But real unit folds in: (1) turn/retry inflation (weaker model → 2–3x turns), (2) tool-call failure tax (silently-wrong output is the expensive failure), (3) review/rework overhead, (4) **clear win on** short/self-checking tasks (logs, doc-gen, naming, compaction, boilerplate, one-file edits), (5) **likely loses on** multi-file refactors w/ cross-file invariants. Decision is the task-class segmentation, not the provider.

## PoC (one afternoon, decisive)
One internal lane on **DeepSeek V4-Flash via native `/anthropic` endpoint (no proxy)** vs a **Sonnet 5 control lane**, on a **frozen 10-task internal suite** (3 log/analysis, 3 doc-gen, 2 single-file refactor, 2 multi-file refactor — include the hard class), acceptance check per task set BEFORE running. Measure: completion rate, cost-per-completed-task, turn/token inflation, review-correction burden, failure-mode breakdown.
- **PASS**: on mechanical subset — completion ≥90% of Claude, cost ≤40%, review burden not materially higher.
- **CONDITIONAL**: passes only on low-fidelity subset → agnosticism for those lanes only.
- **FAIL**: even easy work needs babysitting → abstraction not worth it; bank the Opus→Sonnet downshift.

## Risks
Fidelity tax is real + task-dependent; proxy = new failure surface + maintenance (dead-man's-switch); prices/model-names churn monthly (don't hard-code IDs); benchmarks are aggregator-sourced (measure OUR tasks); vendor-lock → vendor-sprawl (N keys/bills/rate-limits); **data-path discipline** — these are Chinese-lab APIs, keep STRICTLY on internal non-client-data lanes (TENANT-RESIDENCY-001), enforce structurally; **strongest comparator may be Sonnet 5, not Opus** — measure against it or overstate the switch case.

## Recommendation
Scoped pilot, not fleet-wide fabric. Smallest first step = the PoC (DeepSeek V4-Flash native vs Sonnet 5). Two cheap parallel side-bets: (a) price the **GLM flat-rate plan** for always-on lanes (native, near-zero risk); (b) the **zero-risk Opus→Sonnet 5 downshift** on throwaway lanes as the baseline saving. If PoC clears → LiteLLM central gateway for internal lanes only; keep long-horizon refactors on Claude. If it fails on easy tasks → bank the Sonnet downshift, drop the abstraction.
