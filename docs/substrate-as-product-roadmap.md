# Substrate-as-Product Roadmap

*Draft for cai ruling + operator review · cc-orchestrator · 2026-06-23*

## 1. Thesis

The product is **not any single app — it is the substrate**: the always-on multi-agent CC fleet + its governance + its reusable capability pipelines. Apps are outputs; the substrate is the compounding asset. Each lane is a **monetizable vertical**; the substrate is the factory that builds, ships, and operates them with ihsan at a cost and cadence a normal team can't match. The constraint to scale is **coordination, not capability** — we add value fastest by hardening the dispatch/governance core, not by adding more half-tended lanes. Core is a singleton: **cai (strategy/governance) + cc-orchestrator (dispatch/operations)**; everything else is a vertical lane (durable) or an engineer lane (ephemeral).

## 2. The Verticals

| Vertical | What it is | Monetization | Maturity |
|---|---|---|---|
| **ihsanos storefront platform** | Single-brand commerce platform for home-based merchants — shared bot, merchants onboard as orgs; customer shop + merchant Mini App (catalog, orders, PayNow-OCR, capacity caps, fulfilment calendar, variations/add-ons, per-line notes) | Platform fee / per-merchant; the **commerce engine** other verticals plug into | **Live** — customer + merchant web surfaces shipped; isolated demo account; TG Mini App in progress; TG identity-bridge schema applied, route gated for hardening |
| **shipforge.ai** | Conversational website cloner + manager — clients self-maintain by bot; content → clean Next.js/Vercel stack, commerce → our storefront engine | **First revenue workstream** — recurring site build + management | **MVP** — wingmen.dev cutover building (dual-theme, operator-approved "keep both") |
| **cosem (ADCDA)** | Training/attendance/skill-sheets PWA for Abu Dhabi Civil Defence Authority | Client engagement (ADCDA) | **Live** — offline PWA shipped, security SEV-1/3 closed, permissions overhaul P1 merged (P2 building) |
| **HR-for-FIs (Mizuho)** | HR / leave / claims / granular RBAC / hash-chain audit / PDPA suite for financial institutions | FI client contracts (Mizuho pitch live) | **Milestone A** built (synthetic FI demo, pitch-ready); Milestone B building |
| **branditqr** | Live dynamic-QR application | QR SaaS | **Live** — auth + scale remediation staged live-safe |
| **hifz-companion** | Quran-memorization companion | TBD | Active (priority 3) |
| **dawah-pipeline · cosem-video-pipeline** | Content/da'wah + video pipelines | TBD | Specced |

## 3. The Substrate Moat — what compounds

1. **Governance that lets us move fast *and* safely.** Consensus (cc-orch + cai = build authority), mandatory independent review on money/security PRs, draft→review→apply with direct-psycopg prod discipline (decision-962), the **eyeball-gate** (nothing reaches the operator/prod unverified), cai adjudication on forks, §6.6 grants with challenge windows. This is why prod migrations and security fixes ship in hours without incidents.
2. **Reusable capability pipelines.** The "full ihsan pipeline" battery — audit · visual-mapping · synthetic-test+docs · security-hardening · performance — applied to any codebase; plus the mandatory design pipeline (frontend-design + cc-reviewer design dimension, mobile-first). Build a vertical once, **ihsanify it repeatably**.
3. **A self-policing fleet.** Per-lane watchdog (idle/unsent/dialog recovery + SLA) + the new **HUB-BACKLOG backstop** (any lane's owed-response self-surfaces until cleared — closes the "drop a ball during a firehose" failure) + the planned fleet self-audit pass (dups, staleness, zombies, identity integrity). The substrate notices its own cruft.
4. **The lane/identity model.** Durable vertical lanes + ephemeral engineer lanes; distinct identity per vertical; singleton core. Lets capacity flex without drift.

**Hard-won lessons now baked in:** coordination-first scaling (dispatch is the bottleneck — dups appear when it's saturated); identity-drift discipline (distinct identities, never same-base sub-tags sharing a bus); delivery-integrity (verify the *delivery*, not just the build — magic-links die to Telegram link-preview, bypass-tokens 401 cold; cold-verify before relay).

## 4. Near-term priorities + sequencing

1. **Harden the coordination core first** (it's the constraint): land the fleet self-audit pass on top of the watchdog + HUB-BACKLOG backstop; make dispatch reliable so lanes don't self-close on dispatched work or rot queues (now mitigated by FIFO-by-default).
2. **Ship the first revenue: shipforge** — finish the wingmen.dev cutover (proof of the cloner+manager), then a repeatable client-onboarding flow. Revenue validates the thesis.
3. **Complete the storefront commerce engine** — clear the TG identity-bridge wiring gate (DoS/RLS-smoke/WAF + cai sign-off) so merchants onboard frictionlessly via Telegram; this engine is shared infra for shipforge-commerce clients.
4. **HR-for-FIs to first contract** — Milestone B → polish → Mizuho pitch → reference FI client.
5. **Productize the ihsan pipeline** as a named, repeatable "ihsanify a vertical" capability (first target: fastrans) — turn the moat into a service.

## 5. Open questions for cai

1. **Sequencing under one constraint.** Revenue-first (shipforge) vs substrate-hardening-first (self-audit/coordination) vs vertical-depth (storefront bridge / HR-FI)? If coordination is the real bottleneck, does hardening it outrank even revenue in the next phase?
2. **The coordination ceiling.** How many concurrent lanes can cc-orchestrator dispatch + govern before quality degrades (this session hit dropped-ball + self-close failures under firehose)? Do we need a dispatch tier / sub-orchestrators, or hard-cap lane count and queue verticals?
3. **Lane model as verticals multiply.** Durable-vs-ephemeral spin-up policy; the identity registry discipline at 15+ verticals; cost of always-on lanes vs on-demand.
4. **Governance at scale.** Does consensus + eyeball-gate hold as lane count grows, or does it need delegated review tiers (trusted lanes self-merge classes of low-risk change)?
5. **Commercial focus.** Which vertical do we push commercially *first* — shipforge clients, storefront merchants, HR-FIs, or branditqr — and does the substrate sell as a service ("ihsanify your product") in its own right, not just its outputs?
