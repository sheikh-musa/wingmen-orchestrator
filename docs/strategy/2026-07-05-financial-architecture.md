# Financial Architecture — subscription registry + monetization floors

**2026-07-05 · Nazim (orch-console) · operator directive #2299. Substrate-first: the registry is `finance_subscriptions`; `SELECT * FROM finance_burn` is always current. This doc is the human mirror + the derivation of targets.**

## The registry (substrate table `finance_subscriptions`, migration 018)

Discovered live where possible (Supabase org plan=**pro**, 3 projects, via MCP; Vercel teams enumerated; goumlyne confirmed in Gazzabyte's org = **partner-billed, $0 to us**). Amounts marked unconfirmed until the operator blesses them; **standing rule: the registry row is created BEFORE any new subscription is taken.**

| item | est $/mo | status | needs |
|---|---|---|---|
| Claude Max | **222.00 ✓** | active | CONFIRMED #2300: 300 SGD/mo, 20x (≈$222 @ 0.74 fx) |
| Anthropic API (metered) | ~3.00 | active, usage | scales with shipforge customers — unit economics in the 07-05 pressure test; per-gen logging live |
| Supabase Pro org | 45.00 | active | CONFIRM invoice ( $25 base + ~3× micro compute − credit ) |
| Vercel | 20.00 | active | CONFIRM Pro vs Hobby (team `wingmen-aa9356e1`) |
| Firebase/GCP (cosem apps) | **0.00 ✓** | active, usage | CONFIRMED #2300: comfortably inside free tier |
| Domains (wingmen.dev, ihsanos.com, …) | 2.50 | active | CONFIRM full list |
| Zoho Mail | 1.00 | active | CONFIRM plan |
| GitHub / Tailscale / Telegram / Cloudflare | 0.00 | active | free tiers, confirmed |
| DO VPS SGP1 (migration target) | 32.00 | **upcoming** (~Jul 21–Aug 15) | size at provision ($24–48) |

**Burn: $293.50/mo est now ($222 operator-confirmed) → ~$325.50/mo post-VPS.** The `finance_burn` view reports est vs confirmed bands plus `unconfirmed_rows` so drift is always visible. Claude Max is 68% of total burn — the fleet's brain is the cost; the infra is rounding.

## Monetization floors (recurring revenue vs recurring burn — MRR basis)

Unit contributions from the 2026-07-05 pricing pressure test (median-usage COGS deducted): **managed site $29/mo nets ~$26.40 · store $49 nets ~$42 · store $79 nets ~$72 · $29 hook nets ~$22–26 one-off** (pipeline fuel, not MRR).

| target | covers | $/mo | in units |
|---|---|---|---|
| **T0 — infra floor** | everything except Claude Max | ~$100.50 (post-VPS) | **4 site subs** (or 2 store + 1 site) |
| **T1 — full-burn floor** | the whole stack incl. Max | ~$325.50 | **13 site subs**, or 8 store@$49, or 6 site + 4 store |
| **T2 — resilience** | 2× burn (buffer + growth spend) | ~$651 | **25 site subs** or equivalent blend |

Rules of thumb: one store sub ≈ 1.6 site subs of coverage; ~14 hook sales ≈ one month of full burn (one-off, so hooks buy runway while subs build the floor). Client engagements (irsyad, adcda/tdu) sit ABOVE this architecture — they fund growth; the floor targets are what make the fleet self-sustaining on recurring revenue alone.

## Operating policy

1. **Registry-first**: new subscription ⇒ substrate row first (name, amount, owner, confirmed=false until first invoice).
2. **Monthly review** (console body, 1st of month): reconcile amounts vs invoices, flip `confirmed`, re-derive T0–T2 if burn moved ±10%.
3. **War-room digest hook**: once shipforge MRR exists, the hub's daily rollup includes `MRR / T1` coverage %. (Digest directive already filed #6651.)
4. **Variable COGS stays out of fixed burn**: metered API per customer is priced into unit contributions, not the floor — double-counting it would overstate targets.
5. Post-VPS, re-examine Claude Max seats vs API for the fleet itself (MODEL-POLICY-001 territory; today Max is strictly cheaper than metered for fleet workloads).
