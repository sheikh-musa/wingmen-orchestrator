# HBB Feature Roadmap — ihsanos Storefront Platform

**Date:** 2026-07-15
**Author:** orch (research + planning)
**Scope:** Product feature roadmap for home-based businesses (HBBs) — SG home bakers, home F&B, home crafts — on the Wingmen/ihsanos Telegram Mini-App + web commerce platform.
**Nature:** Planning doc. Codebase claims are flagged as INFERRED (from CLAUDE.md / platform context) vs. VERIFY (would need to read ihsanos code). No deep codebase dive was done, per instructions.

---

## 0. Build-vs-Reuse Rubric (the principle)

We apply a single test to every feature:

> **BUILD when the feature is our proprietary data/graph that compounds** — orders, customer identity, the ledger, the HBB directory, loyalty balances. These are the moat; owning them keeps switching costs high and lets features compose (loyalty reads orders, marketing reads identity, discovery reads the directory).
>
> **REUSE when the feature is a commoditized, capital-heavy external service** — courier logistics, mapping/geocoding, payment rails (already the HitPay/Stripe decision), SMS/email. We rent these behind a provider-agnostic abstraction so we can swap providers without touching product code. We never try to out-build a courier fleet or a maps team.

**Corollary — the abstraction pattern is the reuse discipline.** Payments already sit behind a provider-agnostic layer + internal ledger. Delivery must copy that exact shape: a `DeliveryProvider` interface (quote / book / track / cancel / webhook) with Lalamove and pandago as the first two adapters. This keeps "reuse" from becoming lock-in.

---

## 1. Priority-Ranked Feature Table

Effort in **lane-days** (one focused engineer-lane day): S = 1–3, M = 4–8, L = 9+. Priority P0 (launch-critical) → P3 (later).

| # | Feature | Build / Reuse | Tool/API if reuse | Effort | Priority | Key risk / dependency |
|---|---------|---------------|-------------------|--------|----------|-----------------------|
| 1 | **Scheduling / calendar** (lead-times, pickup/delivery slots, daily qty caps, closed-days, pre-order drops) | **BUILD** | — (native; do NOT embed Cal.com/Calendly) | M | **P0** | Tied to order data + inventory. Booking SaaS models appointments, not perishable-goods capacity. Depends on order schema having a fulfillment slot/date. |
| 2 | **Integrated delivery** (on-demand courier at checkout) | **REUSE** behind own abstraction | **Lalamove API** (primary) → **pandago API** (2nd) | L | **P1** | Billing/account model per merchant vs platform (see §2). SG regulatory OK. The big one — full section below. |
| 3 | **Delivery zones / flat-rate + self-delivery** (merchant sets own radius + fee, or "self-deliver") | **BUILD** | — | S | **P0** | Cheap, unblocks launch before courier integration. VERIFY: may already partially exist (storefront settings). |
| 4 | **Customer loyalty** (stamps, points, referrals, store credit, birthday/repeat) | **BUILD** | — (ledger + identity are ours) | M | **P2** | Store credit MUST post to the double-entry ledger (it's a liability). Don't bolt on a loyalty SaaS — it can't see our orders/identity. |
| 5 | **Marketing / retention** — Telegram broadcast to past customers ("this week's menu") | **BUILD** (on Telegram Bot API) | Telegram Bot API (already our channel) | S | **P1** | We already own the bot + customer Telegram IDs. Huge HBB value (drop announcements). Risk: spam/opt-out compliance — needs unsubscribe + rate limits. |
| 6 | **Promos / vouchers / discount codes** | **BUILD** | — | S | **P2** | Must apply at checkout + reconcile to ledger. Straightforward. |
| 7 | **Abandoned-cart nudge** | **BUILD** | Telegram Bot API | S | **P3** | Low effort once broadcast infra exists; depends on cart state persistence. |
| 8 | **Inventory / stock caps + sold-out** | **BUILD** | — | S | **P0** | Perishable HBB goods sell out daily; sold-out state is table-stakes. Overlaps #1 (daily caps). |
| 9 | **Product variants / options** (size, flavour, add-ons) | **BUILD** | — | M | **P1** | Order-items schema must carry variant/modifier. VERIFY current pos_order_items shape. |
| 10 | **Reviews & ratings** (social proof) | **BUILD** | — | M | **P2** | Trust signal, feeds the flagship directory later. Needs moderation. Gate reviews to verified orders. |
| 11 | **Photo galleries** (product/store imagery) | **REUSE** (storage) + thin build | Object storage / CDN (e.g. Supabase Storage / Cloudflare) | S | **P1** | HBB is visual (food/crafts). Image hosting is commodity; just wire it. |
| 12 | **Order-management dashboard** (merchant sees/accepts/updates orders) | **BUILD** | — | M | **P0** | The daily driver for merchants. VERIFY: some order surface likely exists already. |
| 13 | **Analytics** (best sellers, repeat-customer rate, revenue) | **BUILD** | — | M | **P2** | Reads orders/identity we own. Repeat-rate is a killer HBB metric. |
| 14 | **Receipts / invoices** | **BUILD** | — (PDF lib is the only reuse) | S | **P1** | Reads ledger. Unregistered merchants → receipts NOT tax invoices (no GST/UEN); label correctly. |
| 15 | **Refunds** | **BUILD** on payment abstraction | HitPay/Stripe refund APIs | M | **P1** | Must reverse in ledger (double-entry). Depends on payment provider refund support + partial-refund logic. |
| 16 | **🚩 Nearby-HBB discovery + conversational ordering** | **BUILD** (directory/graph) + **REUSE** (maps) | Mapping/geocoding API for display+geo only | L (multi-phase) | **P3 / strategic** | The marketplace/network-effect play. Chicken-and-egg supply, moderation, trust/safety. Full section below. |

---

## 2. Integrated Delivery — Deep Dive (the big one)

**Goal:** at checkout, the customer picks "delivery," we get a live quote, book a courier on confirmation, and track it — for many small, often unregistered merchants.

### 2.1 Provider landscape (SG, verified 2026)

| Provider | On-demand? | Public API? | Account model | Fit for a platform-of-many-merchants | Verdict |
|----------|-----------|-------------|---------------|--------------------------------------|---------|
| **Lalamove** | Yes (immediate + scheduled) | **Yes, self-serve** — v3 REST, public docs, sandbox (`rest.sandbox.lalamove.com/v3`), API key/secret from Partner Portal, HMAC-signed. SG live. | Partner account holds API credentials; wallet/credit billing. | **Best.** Platform holds one account, books on behalf of merchants, rebills. General courier (not food-only) → fits bakers, crafts, F&B. | **PRIMARY — integrate first** |
| **pandago** (foodpanda) | Yes (24/7) | **Yes** — OAuth2 client-credentials + JWT bearer, Brand/Branch model, callback webhooks. SG live. No minimum order, no long-term contract, pay-per-parcel from ~S$6. | Register a **Client as a Brand**, then each outlet as a **Branch**. | **Good, 2nd.** Brand/Branch maps cleanly to platform→merchants. Strong for food/perishables + 24/7. | **SECONDARY — integrate 2nd** |
| **GrabExpress** (Grab) | Yes (same-city, minutes) | Partner API on `developer.grab.com`, OAuth2 — **partnership/approval-gated, not self-serve.** | Partner-issued client credentials via BD relationship. | Strong network, but onboarding is a BD conversation, not a signup. | **DEFER — pursue via BD once volume justifies** |
| **Pickupp** | Same-day island-wide | API exists but less publicly documented for self-serve; AI courier-matching. | — | Same-day, not sub-hour on-demand. | Backup / scheduled option |
| **NinjaVan** | No (next-day/scheduled) | **Most accessible courier API** in SG (aggregators integrate it first). | Standard shipping account. | Scheduled parcels, not hot-food on-demand. | Use only for **non-perishable crafts / scheduled** shipping |
| **EasyParcel / Janio** (aggregators) | EasyParcel = multi-courier incl. on-demand; Janio = cross-border | Aggregator APIs | One integration → many couriers | Tempting shortcut, but adds a margin layer + we lose direct control/branding of the on-demand experience. | Consider later as a **fallback aggregator**, not the primary |

### 2.2 The account/billing model — the load-bearing decision (VERIFY before build)

Two viable shapes; pick deliberately:

1. **Platform-account model** — Wingmen holds ONE Lalamove/pandago account, books all deliveries, pays the courier, and **rebills the merchant** (via our ledger). Cleanest UX (merchant needs no courier account), and it's our ledger's job anyway. **Requires Wingmen to be the contracting business entity** with the courier — fine, since Wingmen is registered even though merchants are not. This is the recommended default.
2. **Per-merchant-account model** — each merchant links their own courier account. Avoids Wingmen carrying courier liability/float, but most unregistered home bakers won't set up a Lalamove business account. Poor fit for our audience.

**Recommendation:** platform-account model, with the courier fee passed through as a distinct ledger line (not merchant revenue). This mirrors the payments abstraction.

**⚠️ FLAG — could not fully verify:**
- Exact **SG per-delivery pricing** for Lalamove (dynamic; needs a live quote in sandbox) and whether Lalamove's terms permit a platform to **book on behalf of third-party merchants under one account** (aggregator/reseller clause). Confirm with `partner.support@lalamove.com` before committing to the platform-account model.
- Whether **pandago** permits many independent third-party merchants as **Branches under one Brand** (vs. requiring each merchant's own Brand/contract). The Brand/Branch model *looks* built for this, but confirm the reseller terms.
- **GrabExpress** access terms and lead time — partnership-gated, treat as a BD task, not an engineering estimate.

### 2.3 Recommended integration order

1. **Lalamove first** — self-serve, sandbox, general-purpose courier, one account rebills many merchants. Fastest path to a working checkout-to-courier flow.
2. **pandago second** — adds 24/7 + food-optimized coverage; Brand/Branch model fits us. Gives redundancy and price competition.
3. **Direct abstraction, not an aggregator, for on-demand** — go direct to the two providers behind our own `DeliveryProvider` interface. Reserve EasyParcel/aggregators as a later fallback and NinjaVan for scheduled non-perishable (crafts) shipping only.

**Do NOT** try to support every courier at launch. Two direct providers behind one abstraction = coverage + redundancy + control of branding/experience.

### 2.4 Regulatory note (delivery)
On-demand courier of food is not itself SFA-licensed at the platform layer; the food-safety obligation sits with the HBB merchant (see §4). No delivery-specific license blocker for us.

---

## 3. 🚩 FLAGSHIP — Nearby-HBB Discovery + Conversational Ordering

This is the network-effect / marketplace play: turn a set of isolated per-merchant storefronts into a **discoverable local marketplace** where customers find nearby home businesses and order conversationally through Telegram.

### 3.1 Data model (BUILD — this is our moat graph)

- **`hbb_directory`** — one row per merchant store: name, category, description, hero image, status (active/paused), trust signals (verified?, review agg).
- **Geo** — approximate location. **Privacy-critical:** home addresses must NOT be public. Store a **precise pickup point privately** (for courier booking) and expose only a **coarse area** (neighbourhood / postal-sector / ~500m-fuzzed point) for discovery. **REUSE a mapping/geocoding API** (geocode postal code → coordinates; render map) — mapping is commodity; the directory graph is ours.
- **`categories`** / tags — home-baker, home-F&B, halal, vegetarian, crafts, etc.
- **Reviews/ratings** (feature #10) feed trust here.
- Discovery reads **existing** `pos_orders` for popularity/repeat signals — the graph compounds on data we already collect.

### 3.2 Discovery UX

- **Map + list**, filtered by category + distance + open-now (reads scheduling from #1) + delivery-available (reads #2).
- Entry points: a Mini-App "explore nearby" tab and a **conversational** "what's near me / any halal bakes today?" bot query.
- Social proof front-and-centre (ratings, "12 ordered this week").

### 3.3 Conversational ordering (maps onto the EXISTING order flow)

Key insight: **conversational ordering is a new front-end onto the same order pipeline — not a new backend.** The bot's job is intent → structured cart, then it calls the same order-creation path that the Mini-App checkout uses (`pos_orders`/`pos_order_items`, same payment abstraction, same delivery abstraction).

- Flow: NL message → intent/entity parse (which merchant, which items, qty, fulfillment) → confirm structured cart → hand to existing checkout (payment + delivery reuse). No parallel order system.
- Start **rules/menu-constrained** (buttons + guided NL), not open-ended LLM free-text, to keep order accuracy high and cost bounded. Add richer NL later.

### 3.4 Build size, moat, risks

- **Build size: L (multi-phase, the largest item).** Directory + geo + discovery UX + conversational layer. But each phase is independently shippable (directory-only first, conversational later).
- **Moat:** the local supply graph + order/identity data + trust signals. Hard to replicate once dense in a neighbourhood. This is the reason the whole platform exists long-term.
- **Risks:**
  - **Chicken-and-egg supply** — a directory with 3 merchants is worthless. Seed density in ONE geography/community first (don't launch nationwide-thin). Discovery is P3 precisely because it needs merchant density that #1–#15 create first.
  - **Moderation** — public directory + reviews need abuse/quality moderation from day one.
  - **Trust & safety for home kitchens** — surface which merchants have completed the SFA Food Safety Course Level 1 (WSQ) as a badge; do not imply platform vouching for food safety.
  - **Privacy** — never expose home addresses (see §3.1).
  - **SFA licensing note** — see §4.

---

## 4. SG Regulatory / Licensing Touchpoints (honest + concrete)

**Home-based food (the core audience):**
- Under SFA's **Home-Based Food Business** scheme, home bakers/cooks generally **do NOT need an SFA food retail licence** — deemed small-scale, low-risk. (Verified, SFA 2026.)
- **But** binding constraints we should encode/surface:
  - **Direct-to-consumer only.** No catering, and **no selling to other retail businesses** (cafes/restaurants). Our platform is D2C, so we're aligned — but we must NOT build a wholesale/B2B ordering path for HBB food merchants.
  - **Banned items** — e.g. raw seafood, ready-to-eat sashimi. Consider a category guard for food merchants.
  - **Food Safety Course Level 1 (WSQ)** is expected; surface as a trust badge (§3.4).
- **Business registration:** many home bakers are unregistered individuals (no ACRA/UEN) — the platform is explicitly built for this (payments onboarding handles unregistered individuals). Consequence: **receipts are receipts, not GST tax invoices** (feature #14). Do not generate UEN/GST fields for unregistered merchants.
- **HDB/URA home-business rules** exist (no external staff, no heavy footfall) but constrain the *merchant's* operation, not our software.

**Crafts / non-food HBB:** lighter regime — general home-based business rules only, no SFA touchpoint.

**Platform posture:** we facilitate D2C sales and surface compliance signals (Food Safety badge, direct-to-consumer only); we are not the licensing authority and should not represent food-safety guarantees.

---

## 5. What's Already in the Repo (reuse our own work — VERIFY, don't rebuild)

INFERRED from platform context; each should be checked in ihsanos code before building:
- **Order pipeline** — `pos_orders` / `pos_order_items` exist. Variants/modifiers (#9), scheduling slot (#1), and conversational ordering (§3.3) should extend this schema, not fork it. **VERIFY current schema shape.**
- **Payment abstraction + ledger** — provider-agnostic (HitPay/Stripe) + double-entry ledger exists. Refunds (#15), store credit (#4), promos (#6), courier-fee passthrough (#2) all post here. **Reuse, do not add a second money system.**
- **Telegram identity + bot channel** — already ours. Broadcast (#5), abandoned-cart (#7), and conversational ordering (§3) build directly on it.
- **Storefront settings / delivery zones** — a per-merchant settings surface likely exists; **flat-rate delivery zones (#3) may already be partially built.** VERIFY before building — this is the most likely existing-work overlap.
- **Order surface** — some merchant order view likely exists; the dashboard (#12) may be an extension, not a greenfield build.

**Action:** before Phase 1 build, do a 1-lane-day audit of ihsanos for (a) order schema, (b) storefront/delivery settings, (c) any existing merchant order dashboard. Cheaper than rebuilding.

---

## 6. Phased Sequence

Grounded in "ship immediate HBB value on what exists; flagship once supply is dense."

**Phase 0 — Repo audit (before committing estimates):** confirm order schema, existing delivery-zone/settings, existing order dashboard. ~1 lane-day.

**Phase 1 — "A home baker can actually run their day" (P0, immediate value):**
- Scheduling/calendar (#1), inventory/sold-out (#8), flat-rate delivery zones + self-deliver (#3), order-management dashboard (#12).
- *Why first:* these are the daily-driver gaps that block real HBB operation and need no external integration.

**Phase 2 — "Sell more, look pro" (P1):**
- Product variants (#9), photo galleries (#11), Telegram broadcast/drops (#5), receipts (#14), refunds (#15).
- Broadcast (#5) is the highest-ROI retention lever and is cheap on infra we own.

**Phase 3 — "Integrated delivery" (P1, parallelizable with late Phase 2):**
- Delivery abstraction + **Lalamove** adapter, then **pandago**. Gated on the §2.2 account/terms verification (BD/support task runs in parallel with engineering).

**Phase 4 — "Grow the basket / retention" (P2):**
- Loyalty (#4), promos/vouchers (#6), reviews & ratings (#10), analytics (#13), abandoned-cart (#7).
- Loyalty + reviews also seed trust signals the flagship needs.

**Phase 5 — 🚩 Flagship: discovery + conversational ordering (P3 / strategic):**
- Directory (#16 data model) → discovery UX → conversational ordering. Launch **dense in one community first.** Reuses reviews (#10), scheduling (#1), delivery (#2) already built.

**Sequencing logic:** merchant-side operational features (Phase 1–2) create the supply and data density that make the marketplace flagship (Phase 5) non-empty. Building the flagship first would strand it on thin supply.

---

## 7. Open Items to Verify (do not build on these unconfirmed)

1. **Lalamove SG** — reseller/on-behalf-of terms + live pricing (contact partner.support@lalamove.com; test in sandbox). [Delivery blocker]
2. **pandago** — whether many third-party merchants can be Branches under one Wingmen Brand. [Delivery 2nd-provider]
3. **GrabExpress** — access terms + BD lead time (partnership-gated). [Deferred]
4. **ihsanos repo** — order schema, existing delivery-zone/settings, existing order dashboard (Phase 0 audit). [Reuse-our-own-work]

---

### Sources (delivery + regulatory, verified 2026-07-15)
- Lalamove API — https://developers.lalamove.com/ ; SG API solutions https://www.lalamove.com/en-sg/business/api-solutions
- pandago On-Demand Rider API — https://on-demand-rider-docs.deliveryhero.io/ ; https://pandago.sg/
- GrabExpress / Grab developer — https://help.grab.com/merchant/en-sg/4404599733273-What-is-GrabExpress-API ; developer.grab.com
- Aggregators / couriers — https://easyparcel.com/sg/ ; https://sg.pickupp.io/en/
- SFA home-based food — https://www.sfa.gov.sg/food-retail/businesses-that-currently-do-not-need-licence-permit/home-based-food-businesses/requirements-for-home-based-food-businesses ; https://singaporelegaladvice.com/law-articles/home-baked-goods-licence-singapore/
