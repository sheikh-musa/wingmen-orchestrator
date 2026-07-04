# Shipforge/Storefront Pricing Pressure Test — floor LOCKED on measured cost

**2026-07-05 · Nazim (orch-console) · closes the PENDING item in the CTO pricing strategy; satisfies SHIPFORGE-MODEL-001's "measure the REAL cost per full deliverable" mandate using the lane's own instrumentation.**

## Sources (all real, none estimated by this doc)

- **Measured** — cc-shipforge cost instrumentation live since 06-29 (commit e5512ea; `worker/logs/costs.jsonl`, authed `GET /costs`), bus #4827: **single-pass bespoke = $0.149375 exact** (4,705 in + 5,034 out, Opus 4.8); **multipass (scripted 3-call, Majlis-Gold quality) ≈ $0.45** (3× single; self-corrects to exact on next logged multipass run); **faithful clone ≈ $0 LLM**; reroll $0.15–0.45. Earlier estimates were 2–4× too high (#4816 → corrected by #4827).
- **Ceiling case** — agent-iterated bespoke (design-agent, 62 tool calls): ~$3–6 (#4816). NOT the pipeline cost; relevant only if scripted multipass can't hit the quality bar.
- **API rates** (claude-api reference, cached 2026-06-24): Opus 4.8 $5/$25 per MTok (cache-write $6.25, cache-read $0.50); Sonnet 5 intro $2/$10 (thru 2026-08-31, then $3/$15); Haiku 4.5 $1/$5. Batch −50% (not usable — previews/edits are interactive). Serving already runs METERED API (worker logs real $), cleanly separate from the fleet's Max subscription — no scale crossover cliff.
- **Pilot live** — hadramawtkitchen-sg.vercel.app + concierge change-log stood up 07-04 (#6524): the instrument for real edit-frequency data.

## Model (assumption made explicit)

Managed-by-chat **edit** ≈ 20k cached site-context + 3k fresh in + 3k out → Haiku $0.02 / Sonnet $0.04 / Opus $0.10 per edit; **routed blend** (60% Haiku, 35% Sonnet, 5% Opus) = **$0.031/edit**. This is modeled, not yet measured — the concierge log tallies it for real. Infra $2/mo/customer early (Vercel Pro amortized + domain + monitoring), falls with scale. Stripe 2.9% + $0.30.

## A. The $29 hook (one-off deliverable)

COGS = multipass $0.45 + 20% reroll allowance = **$0.54**. Loaded free preview (single-pass + 3 taste-edits) = **$0.27**.

| preview→paid conversion | preview CAC | net per sale | margin |
|---|---|---|---|
| 1% | $27.00 | $0.32 | 1% |
| 5% | $5.40 | $21.92 | 76% |
| 10% | $2.70 | $24.62 | 85% |
| 20% | $1.35 | $25.97 | 90% |

**Verdict: unit cost is a non-issue; CONVERSION is the only variable that can kill the hook.** Below ~2% conversion the free previews eat the sale. cai's hook-first sequence is exactly the right test — the pilot's metric to watch is preview→paid %, not cost.

## B. Managed site subscription ($/mo)

| usage | edits/mo | COGS (routed) | margin @ $19 floor | @ $29 rec | all-Opus COGS |
|---|---|---|---|---|---|
| light | 5 | $2.15 | 89% | 93% | $2.50 |
| median | 20 | $2.62 | 86% | **91%** | $4.00 |
| heavy | 100 | $5.10 | 73% | 82% | $12.00 |
| abuse | 500 | $17.50 | 8% | 40% | $52.00 — underwater at every tier |

The strategy memo's ~$3–8/mo cost estimate and ~70%+ floor-margin claim are **VALIDATED** (median $2.62, heavy $5.10).

## C. Managed store ($/mo; 2× edit volume + 30–100 order-queries + storefront infra)

| usage | COGS | @ $49 | @ $79 |
|---|---|---|---|
| median | $6.84 | 86% | 91% |
| heavy | $13.20 | 73% | 83% |

## D. Fixed base & breakeven

~$79/mo (Vercel Pro + Supabase + VPS worker + misc) → breakeven ≈ **4 median site subscriptions**. Not a constraint.

## LOCKED PRICING (recommendation)

- **Hook: $29 all-in** — unchanged, per SHIPFORGE-MODEL-001.
- **Managed site: $29/mo** (annual = 2 months free, $290). **$19 floor is validated but do NOT sit on it** — price on value (replaces a $100–300/mo developer); floor exists as promo/regional headroom only.
- **Managed store: $49/mo launch; $79/mo** once storefront ops-by-chat (orders/stock queries) is composed in. Same annual structure.
- Sell residency/exportability as trust ("your site is yours, export anytime") — the differentiator Wix/Shopify structurally can't copy.

## Conditions that MUST ship with the subscription (the only real kill-scenario is unbounded chat)

1. **Model routing** — Haiku/Sonnet default, Opus escalation only (abuse case drops $52 → $17.50).
2. **Fair-use cap** — e.g. 100 managed changes/mo, soft-throttle + upsell beyond (turns the 500-edit abuser from 8%-margin into an upgrade conversation).
3. **Email-gated, rate-limited previews** (cai already mandated email-before-preview; add 1/domain + rate limit — unmetered $0.15 previews are the bot-flood surface).

## Caveats / self-correction hooks

- Multipass $0.45 is 3×-single estimate → **exact on next logged multipass** (ask filed to hub).
- Edit cost is modeled → **concierge log on hadramawtkitchen measures real edit frequency + should log tokens per edit** (ask filed to hub).
- If scripted multipass can't hit Majlis-Gold quality and deliverables need agent iteration ($3–6), hook margin drops to ~79% — still healthy; the fix is productizing the multipass, already the lane's stated plan.
- Margins exclude operator/concierge human time (deliberate at pilot stage) and paid marketing beyond free previews.
- Sonnet intro pricing ends 2026-08-31 (+50% on Sonnet-routed edits → median COGS rises ~$0.30/mo; immaterial).
- **cai's sequence is intact**: this LOCKS THE FLOOR only. Subscription build remains gated on hook conversion + waitlist evidence per SHIPFORGE-MODEL-001.
