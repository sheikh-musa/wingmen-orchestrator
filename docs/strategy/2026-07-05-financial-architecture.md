# Financial Architecture — subscription registry + monetization floors

**2026-07-05 · Nazim (orch-console) · operator directive #2299. Substrate-first: the registry is `finance_subscriptions`; `SELECT * FROM finance_burn` is always current. This doc is the human mirror + the derivation of targets.**

## The registry (substrate table `finance_subscriptions`, migration 018)

Discovered live where possible (Supabase org plan=**pro**, 3 projects, via MCP; Vercel teams enumerated; goumlyne confirmed in Gazzabyte's org = **partner-billed, $0 to us**). Amounts marked unconfirmed until the operator blesses them; **standing rule: the registry row is created BEFORE any new subscription is taken.**

**Currency: SGD** (operator directive #2301, migration 019). USD-billed vendors converted @ **1.35 SGD/USD** until each invoice is confirmed; fx refreshed at the monthly review. Product PRICING stays USD (international market) and bridges at the same rate.

| item | est S$/mo | status | needs |
|---|---|---|---|
| Claude Max 20x | **300.00 ✓** | active | CONFIRMED #2300 (native SGD) |
| Anthropic API (metered) | ~4.05 | active, usage | scales with shipforge customers — unit economics in the 07-05 pressure test; per-gen logging live |
| Supabase Pro org | 60.75 | active | CONFIRM invoice (US$25 base + ~3× micro compute − credit) |
| Vercel | 27.00 | active | CONFIRM Pro vs Hobby (team `wingmen-aa9356e1`) |
| Firebase/GCP (cosem apps) | **0.00 ✓** | active, usage | CONFIRMED #2300: comfortably inside free tier |
| Domains (wingmen.dev, ihsanos.com, …) | 3.40 | active | CONFIRM full list |
| Zoho Mail | 1.35 | active | CONFIRM plan |
| GitHub / Tailscale / Telegram / Cloudflare | 0.00 | active | free tiers, confirmed |
| DO VPS SGP1 (migration target) | 43.20 | **upcoming** (~Jul 21–Aug 15) | size at provision (S$32–65) |

**Burn: S$396.55/mo est now (S$300 operator-confirmed) → ~S$439.75/mo post-VPS.** The `finance_burn` view reports est vs confirmed bands plus `unconfirmed_rows` so drift is always visible. Claude Max is ~70% of total burn — the fleet's brain is the cost; the infra is rounding.

## Monetization floors (recurring revenue vs recurring burn — MRR basis)

Unit contributions from the 2026-07-05 pricing pressure test (median-usage COGS deducted, bridged @1.35): **managed site US$29/mo nets ~S$35.60 · store US$49 nets ~S$56.70 · store US$79 nets ~S$97 · US$29 hook nets ~S$30–35 one-off** (pipeline fuel, not MRR).

| target | covers | S$/mo | in units |
|---|---|---|---|
| **T0 — infra floor** | everything except Claude Max | ~S$139.75 (post-VPS) | **4 site subs** (or 2 store + 1 site) |
| **T1 — full-burn floor** | the whole stack incl. Max | ~S$439.75 | **13 site subs**, or 8 store@$49, or 6 site + 4 store |
| **T2 — resilience** | 2× burn (buffer + growth spend) | ~S$880 | **25 site subs** or equivalent blend |

Rules of thumb: one store sub ≈ 1.6 site subs of coverage; ~14 hook sales ≈ one month of full burn (one-off, so hooks buy runway while subs build the floor). Client engagements (irsyad, adcda/tdu) sit ABOVE this architecture — they fund growth; the floor targets are what make the fleet self-sustaining on recurring revenue alone.

## Operating policy

1. **Registry-first**: new subscription ⇒ substrate row first (name, amount, owner, confirmed=false until first invoice).
2. **Monthly review** (console body, 1st of month): reconcile amounts vs invoices, flip `confirmed`, re-derive T0–T2 if burn moved ±10%.
3. **War-room digest hook**: once shipforge MRR exists, the hub's daily rollup includes `MRR / T1` coverage %. (Digest directive already filed #6651.)
4. **Variable COGS stays out of fixed burn**: metered API per customer is priced into unit contributions, not the floor — double-counting it would overstate targets.
5. Post-VPS, re-examine Claude Max seats vs API for the fleet itself (MODEL-POLICY-001 territory; today Max is strictly cheaper than metered for fleet workloads).
6. **Ledger in SGD, product pricing in USD** — bridge at the monthly-reviewed fx (currently 1.35). Never mix currencies in one figure without the S$/US$ prefix.
7. **Every metered API key maps to a NAMED consumer** with an expected burn (shipforge worker → costs.jsonl receipts; bot responders; Stagehand). Unexplained API usage = incident, not noise — same attribution discipline as console writes. Daily API draw rides the digest during rations.
8. **Claude Max weekly cap is a BUDGET, rationed like the allocation** (first applied 2026-07-05, #2305: 75% @ 61% window): if all-models >70% before day 5 of the window → hub downshifts routine work to Sonnet 5 (MODEL-POLICY-001), defers non-showcase background burn past the reset, showcase lanes keep Opus priority; daily digest carries cap-% during rations. Extra-usage credits = crunch-week insurance only, spend logged to the ledger when used.

## Hosting architecture — the three-planes principle (standing)

- **Control/custody plane** (substrate, orchestrator, leases, client data paths): boring rented cloud, SG-region, wingmen-paid, never client-funded — this plane IS the neutral-custody claim. VPS now; note the substrate project currently sits in ap-southeast-2 (Sydney) — migrating it to ap-southeast-1 (SG) is a future item for the sovereignty story.
- **Muscle plane** (Claude lanes, browser workers, batch): owned Macs wherever power/network are reliable. The Studio today; a **Gazzabyte co-shared office is a good UPGRADE for this plane** (business-grade power/net beats residential) — but it is partner-premises: whoever houses the box controls the box, so nothing custody-critical moves there, and the office is a convenience, never a dependency.
- **Client-billed plane** (TDU box, future client workloads): client-billed cloud with managed-infra margin. Clean ownership lines, CoI-attestable.
- **Colocation (SG rack, ~S$150–400/mo)**: NOT yet — it costs more than cloud for less flexibility at our scale. Trigger to revisit (decision memo, not calendar): **a signed contract that requires owned-metal custody in SG jurisdiction** (the R3-fixed sovereignty sell) **or fleet compute outgrowing the Studio**. Until one fires, colo is takalluf.
