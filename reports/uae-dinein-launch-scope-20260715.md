# UAE Dine-In QR + KDS + Food-Truck Preorder — Launch Scope & Plan

**Date:** 2026-07-15
**Author:** cc-orchestrator (research + planning)
**Status:** DRAFT for operator/cai review — NOT approved to build
**Market:** Abu Dhabi / UAE (federal), with DIFC/ADGM noted as separate data regimes
**Platform:** Wingmen / ihsanos commerce stack

---

## 0. TL;DR

The operator's read is correct and is our wedge: UAE F&B has near-universal **QR digital menus** on tables but the ordering itself is still verbal/waiter or aggregator-app. There is a real gap for **QR menu + in-place ordering + kitchen routing**, and our existing order engine (`pos_orders` with `fulfillment_type=dine_in` already present, live Kanban board, provider-agnostic payments, GL) means we can ship this as a **thin new surface, not a new backend.**

- **Ship first:** Dine-in QR ordering (reuses ~80% of the order pipeline). New = `tables`/QR model + table-scoped order session.
- **Ship close behind:** KDS as a **new view** on the existing order board + realtime + a couple of status/timestamp fields. Almost no new backend.
- **Ship third:** Food-truck preorder = existing `pickup` + `scheduled_time` flow + a "mobile store" (location + operating windows) model.
- **Hard gate before ANY UAE merchant onboards:** a **residency decision** (TENANT-RESIDENCY-001). PDPL is extraterritorial and there is **no managed Supabase UAE/GCC region today** — this needs an explicit cai/operator grant on where UAE data lives. See §4.4.
- **Biggest external unknown:** which payment provider gives us **platform/marketplace split-settlement for small/unregistered merchants** in the UAE (the HitPay-equivalent). Several candidates; all need a partner call. See §4.2.

---

## 1. Capability 1 — Dine-In QR Ordering

**Flow:** customer scans a table QR → store menu opens scoped to that table → orders → pays online **or** pay-at-counter → order lands on the kitchen/board with `fulfillment_type=dine_in`, table reference attached → party can "order more to this table" during the sitting.

### Build-vs-reuse

| Piece | New / Reuse | Notes |
|---|---|---|
| `fulfillment_type = dine_in` | **REUSE** | Already exists in `pos_orders`. |
| Menu, variants, modifiers, coupons | **REUSE** | Existing storefront/menu + `pos_order_items`. |
| Payment (online) | **REUSE** | Existing provider abstraction (see UAE provider swap, §4.2). |
| Status enum + board | **REUSE** | pending→confirmed→preparing→ready→completed. |
| GL posting | **REUSE** | `post_journal_atomic` / `gl_*`. |
| **`tables` model + QR** | **NEW** | Per-store table registry: `table_id`, label/number, QR token, seats (opt), zone (opt). QR encodes store + signed table token (not a raw ID — prevent spoofing/enumeration). |
| **Table-scoped order session** | **NEW** | Bind an open order (or set of orders) to a table for the duration of a sitting; support multiple additive orders to the same table ("order more"), running tab, and a close/settle. |
| **"Pay at counter" path** | **NEW-ish** | Order can be created unpaid, `status=confirmed`, settled later on the board's mark-paid. Board already supports mark-paid; the new bit is a first-class dine-in "open tab → settle" concept vs one-shot checkout. |
| **Session lifecycle / table state** | **NEW** | free / seated / awaiting-payment / clearing. Lightweight — can live on the `tables` row or a `table_sessions` row. |

**Effort:** **M** (order engine reuse carries most of it; new work is the table/session model + QR + tab settle).
**Priority:** **P1 — ships first.** This is the wedge.

**Design guardrails**
- Keep it a **thin surface**: no fork of the order pipeline. A dine-in order is a normal `pos_orders` row with a table reference + session id.
- QR token must be signed/opaque so a diner can't tamper table or store scope.
- "Order more to this table" = new `pos_orders` rows linked by `table_session_id`, so the kitchen sees each course as it's fired but the front-of-house can settle the whole tab.
- Sushitei migration note: a sit-down chain is exactly this shape — table QR + repeat firing + one bill. Good design-partner/dogfood candidate once the SG scan completes.

---

## 2. Capability 2 — Kitchen Display System (KDS)

**Design principle:** a **new VIEW on the existing order board + realtime**, NOT a new backend. The kitchen screen is a reformatting of `pos_orders`/`pos_order_items` already flowing through the board, optimized for a kitchen (large tickets, timers, bump).

### Build-vs-reuse

| Piece | New / Reuse | Notes |
|---|---|---|
| Order/item data | **REUSE** | `pos_orders` / `pos_order_items`. |
| Realtime push | **REUSE** | Same realtime the board uses; KDS subscribes to the same stream. |
| Status transitions | **REUSE / EXTEND** | Core enum exists (preparing/ready). KDS needs a couple more granular states — see below. |
| **Ticket states for kitchen** | **NEW (small)** | new → preparing → ready → **served/bumped**. Map to existing enum where possible; add `served` (or reuse `completed` for counter, add a dine-in "served" distinct from "paid/completed"). Decide: extend enum vs a KDS-local sub-status. Recommend a small additive status/flag, not a fork. |
| **Timing fields** | **NEW (small)** | `fired_at`, `ready_at`, `bumped_at` timestamps → drive age timers + SLA color (green/amber/red by elapsed). Some may already exist as status-change timestamps; add the missing ones. |
| **Item-level bump / station routing** | **NEW (optional, later)** | Route items to stations (hot/cold/bar) and bump per-station. Phase-2; not needed for MVP. |
| **KDS screen UI** | **NEW** | Kitchen-optimized layout: big cards, color-by-age, tap-to-advance/bump, "recall". A new frontend view, existing data. |

**Effort:** **S–M** (mostly frontend + 2–3 fields; no new backend).
**Priority:** **P1.5 — ships close behind dine-in** (a dine-in launch without a kitchen surface just pushes tickets to the generic board, which works but is not the pitch). Pair it with capability 1 for the demo.

**Fields/states to add (concrete):**
- Timestamps: `fired_at`, `ready_at`, `served_at` (and/or `bumped_at`) on the order (and optionally per-item for station bump later).
- A dine-in-meaningful terminal distinction: `ready` (kitchen done) vs `served` (delivered to table) vs `completed` (tab settled). Today's enum collapses some of these — decide the minimal additive change rather than reworking the enum.

---

## 3. Capability 3 — Food-Truck Preorder

**Flow:** customer orders ahead → picks a pickup window → pays → collects at the truck's current location. A truck = a **mobile store** with a **current location** + **operating windows**.

### Build-vs-reuse

| Piece | New / Reuse | Notes |
|---|---|---|
| `fulfillment_type = pickup` | **REUSE** | Already exists. |
| `scheduled_time` (order-ahead) | **REUSE** | Already exists — this is the whole preorder mechanic. |
| Menu / payment / board / GL | **REUSE** | Same as above. |
| **Truck = mobile store** | **NEW (small)** | A store with `is_mobile` + a **current location** (lat/lng, updatable) + **operating windows** (schedule of where/when open). |
| **Location/schedule publishing** | **NEW (small)** | Storefront shows "where is the truck now / next" + which windows are orderable. Ties `scheduled_time` slots to the truck's open windows so you can't preorder for a window the truck isn't parked. |
| **Pickup-ready notification** | **REUSE / small** | Ready state already exists (board/KDS); surface a "come collect" ping to the customer (Telegram/web). |

**Effort:** **S–M** (biggest new piece is the mobile-store location + windows model; ordering is entirely the existing pickup + scheduled flow).
**Priority:** **P2 — ships third.** Smaller market than dine-in; validates "mobile store" primitive that also helps pop-ups/markets.

---

## 4. UAE Market-Entry Checklist

> Research is current as of 2026-07-15. Verified items are sourced (§7). Items marked **⚑ VERIFY** need a partner call or primary-source confirmation before we rely on them.

### 4.1 Currency & VAT

- **Currency:** AED. Price, charge, and settle in AED.
- **VAT rate:** **5%** (confirmed for 2026). Standard-rated for restaurant food/dine-in service.
- **Registration threshold:** **mandatory at AED 375,000** annual taxable supplies; **voluntary from AED 187,500.** Many small merchants we onboard will be **below threshold / unregistered** — receipts for them must NOT show VAT/TRN. Our receipt engine must be **per-merchant VAT-aware** (registered vs not).
- **Receipt/invoice requirements (registered merchants):** show "Tax Invoice", supplier + TRN, invoice number + date, item description/qty/unit price, total ex-VAT, VAT rate + amount, gross total. **Simplified tax invoice** allowed for supplies ≤ AED 10,000 (covers essentially all F&B tickets); **full tax invoice** required above that.
- **Records:** retain invoices/receipts **5 years**.
- **⚑ E-invoicing (watch, not blocking for MVP):** UAE national e-invoicing rollout — **pilot from July 2026, first mandatory deadline Jan 2027**, Peppol "5-corner" model, structured **XML/JSON** via Accredited Service Providers (ASPs). Not required to launch, but our receipt/invoice layer should be built so a Peppol/ASP path can be added. Flag for a 2026-H2 follow-up. **⚑ VERIFY** exact scope/who's in-scope for the first wave.

**Implication:** receipt engine needs a UAE profile: AED formatting, 5% VAT line, TRN when present, "Tax Invoice"/"Simplified Tax Invoice" labeling, and a clean unregistered-merchant mode. Effort **S–M**.

### 4.2 Payment Rails (mirror of the SG HitPay analysis)

**UAE does NOT use PayNow.** The rails are cards + wallets + regional gateways. Cash is falling fast (national **90%-cashless-by-end-2026** push).

**Consumer methods to accept:**
- **Cards:** Visa / Mastercard (dominant). **⚑ VERIFY** domestic scheme **Jaywan** (UAE national card scheme by Al Etihad Payments) support/requirement timeline.
- **Wallets:** **Apple Pay, Google Pay, Samsung Wallet** widely accepted; local: **Careem Pay, e& money, Payit, Botim, Ziina.**
- **BNPL:** **Tabby** (and Tamara) common; several gateways pre-integrate Tabby.

**Gateway/provider candidates (map to our provider abstraction):**

| Provider | Fit for us | Split/marketplace settlement | Notes / verify |
|---|---|---|---|
| **Stripe (UAE)** | Best DX; already in our abstraction | **Connect is restricted/limited in UAE** ⚑ | Basic pay + payouts work; **marketplace payouts / sub-merchant split may be unavailable or gated.** Must confirm with Stripe UAE. This is the key question for our platform model. |
| **Telr** | Strong for < AED 200k/mo UAE-centric merchants; pre-integrated Tabby | ⚑ VERIFY split/sub-merchant | Predictable pricing (~2.49–2.69%), regional support. |
| **PayTabs** | Best for regional/marketplace across GCC (KSA/KW/UAE) | **Marketed for marketplaces** ⚑ | ~2.0–4.0%. Likely our strongest split-settlement candidate — confirm sub-merchant onboarding for small/unregistered sellers. |
| **Network International / Magnati** | Best acquiring at scale (> AED 500k/mo) | ⚑ | 1.5–3.5% / 1.8–3.8%. Bank-grade acquiring; heavier onboarding. |
| **Checkout.com** | International card volume | ⚑ | Overkill unless cross-border. |
| **Tap / Mamo** | SMB-friendly, quick onboarding | ⚑ VERIFY split | Good for fast small-merchant onboarding. |

**Recommendation (provisional, pending partner calls):**
- **Primary candidate for the platform/marketplace split model: PayTabs** (explicitly marketplace-oriented, GCC-wide) — **⚑ confirm sub-merchant split-settlement + KYC for small/unregistered merchants.**
- **Keep Stripe** in the abstraction for merchants who qualify and for DX, **but do NOT assume Stripe Connect works for our marketplace settlement in UAE — verify first.**
- **Telr or Tap** as the fast-onboarding path for tiny single merchants who don't need split.
- Because we have a provider-agnostic payment abstraction, the UAE launch is a **new provider adapter + a settlement-model decision**, not a payments rebuild. The one thing the abstraction must express well is **platform-collects-then-splits vs merchant-of-record-per-seller** — decide this explicitly (it drives which provider).

**⚑ Open payment question (needs a partner/compliance call):** for merchants **below the VAT threshold / not formally licensed**, which provider will actually onboard them as sub-merchants, and does the platform become merchant-of-record (and thus liable)? This is the single biggest go/no-go unknown on payments. Mirror the SG HitPay platform-account structure but do not assume it maps 1:1.

### 4.3 Delivery Landscape (for the `DeliveryProvider` abstraction)

- **Aggregators (own the customer + courier, closed):** **Talabat (~45% share), Deliveroo (~25%), Careem, Noon Food**, plus **Keeta** (Meituan-backed, launched Dubai Sep 2025, growing fast). These are marketplaces, not courier APIs — a restaurant on them rents their demand. Not what our abstraction plugs into for *our own* orders.
- **On-demand courier / delivery-as-a-service APIs (the UAE analog of Lalamove/pandago) for OUR orders:**
  - **Careem Express — "Flash Delivery" API** (45–60 min on-demand, live GPS tracking, "Deliver Now" at checkout). **Strongest DeliveryProvider candidate.** ⚑ VERIFY direct API access terms vs going through an integrator.
  - **Deliverect** (middleware) offers a **Careem Express** integration and connects order/POS systems to couriers — a possible fast path to Careem Express without a direct integration. ⚑ VERIFY.
  - **Quiqup**, **Careem Box**, and other local last-mile couriers exist for structured/on-demand delivery. ⚑ VERIFY which expose a clean business API.
- **Design note:** delivery is **not on the MVP critical path** (dine-in and food-truck pickup need no courier). Build the UAE `DeliveryProvider` adapter around **Careem Express Flash Delivery** when we get to delivery fulfillment; treat aggregators as an out-of-scope channel (or a later "receive orders from Talabat" ingestion, which is a different problem).

### 4.4 Data Residency — **RESIDENCY GATE (TENANT-RESIDENCY-001)** ⚑ DECISION REQUIRED

**This is a hard gate before any UAE merchant/customer data is written.**

- **Law:** UAE **PDPL** (Federal Decree-Law No. 45/2021, in force since 2 Jan 2022) is **extraterritorial** — it applies to us because we'd be **offering services to UAE residents** even without a UAE entity. UAE Data Office enforcement escalated through 2025–2026.
- **General localization:** at the **federal** level, cross-border transfer is **permitted where the destination gives adequate protection** — so there is **no blanket "all data must stay in UAE" rule for general commerce.** BUT:
  - **Sectoral hard-localization:** the **Central Bank Consumer Protection Standards** require **financial/transaction data** of licensed FIs to be **stored in the UAE**, with cross-border transfer needing CBUAE approval + consumer consent. If our payment/ledger data is deemed in-scope, that pushes toward in-region storage. **⚑ VERIFY** whether a commerce platform (not a licensed FI) is caught by this.
  - **DIFC / ADGM** (Abu Dhabi Global Market) have **separate** data regimes with their own adequacy lists — relevant since we're targeting **Abu Dhabi**; a merchant licensed in ADGM may sit under ADGM's regime, not federal PDPL.
- **Infra reality:** **There is NO managed Supabase region in the UAE or GCC today** (open feature request only). AWS has **me-central-1 (UAE)** and **me-south-1 (Bahrain)**, but Supabase-managed doesn't offer them. Options:
  1. **Keep UAE data in the existing SG/US `ceayj` (multi-tenant) DB** — simplest, but **violates the spirit of a residency decision** and likely unacceptable for financial data / a real client. Requires an explicit, expiring residency exception (operator+cai grant). **Not recommended as the durable answer.**
  2. **Provision a dedicated UAE tenant silo** (mirrors the irsyad/goumlyne silo pattern) in the **nearest compliant Supabase region** (EU/closest available) — better data-isolation story, still not physically in-UAE.
  3. **Self-hosted / third-party managed Supabase on UAE (me-central-1 / Dubai/AbuDhabi) infrastructure** — the only path that gives true in-UAE residency; higher ops burden. Third-party managed-Supabase-in-UAE providers exist.

**Required action:** raise a **residency decision to cai + operator BEFORE the first UAE data write** (LAYER-VOCAB-001: name the exact store + project ref once chosen). Recommend: **provision a dedicated UAE silo (option 2 minimum; option 3 if financial-data localization applies)**, never commingle UAE rows into `ceayjeamtmcyzzvqflus`. Treat any interim use of `ceayj` as a time-boxed exception requiring the joint grant. **⚑ This is the top blocking item; do not onboard a UAE merchant until it's decided.**

### 4.5 Arabic / RTL Localization

- **i18n:** en/ar scaffolding already exists in some repos — leverage it. Effort is **not** "build i18n from scratch."
- **What it actually takes:**
  - **RTL layout** across storefront + menu + receipts + KDS (mirroring, icon direction, alignment) — the real work; **M**.
  - **Arabic content**: menu items/descriptions are merchant-supplied — need bilingual entry in the merchant tooling; UI chrome strings need Arabic translation. **S–M**.
  - **Number/date/currency formats**: Arabic locale, AED formatting, Hijri date option on receipts (⚑ VERIFY if required — likely optional). **S**.
  - **KDS** can likely stay operational-English for kitchen staff to reduce scope; storefront/receipt must be bilingual. Decide per surface.
- **Effort:** **M** overall, front-loaded on RTL layout. Not a launch blocker for a single English-first pilot merchant, but required for the general UAE market.

### 4.6 Go-to-Market / Competition

- **Who owns UAE F&B ordering today:** the **aggregators** (Talabat/Deliveroo/Careem/Noon/Keeta) own **delivery** demand. For **on-premise dine-in**, ordering is mostly **QR-menu-then-waiter** — the menu is digital but the order is not. That's the **wedge**.
- **Our wedge:** "You already put a QR on the table — make it actually take the order and fire the kitchen." Low behavior change for the diner (they already scan), high value for the merchant (fewer waiter trips, faster table turns, upsell via modifiers, no aggregator commission on dine-in).
- **Adjacent competitors:** dedicated QR-ordering/KDS SaaS exists globally and some regionally (and POS vendors bundle KDS). Our edge is **one stack** (dine-in + pickup + delivery + food-truck on the same order engine, ledger, and Telegram/web surface) and **fast bespoke onboarding**.
- **Honest risks:**
  - **Payments/settlement for small merchants** may be harder to onboard than SG (KYC, merchant-of-record liability) — §4.2 unknown.
  - **Residency + PDPL** adds real setup cost per §4.4; can't be skipped for a real client.
  - **Merchant habit**: many UAE restaurants are locked into an incumbent POS; we're an add-on surface unless we also cover POS. Position as "QR ordering that plugs into your day," not "rip out your POS."
  - **Aggregator gravity**: for delivery, we don't beat Talabat's demand — stay focused on **dine-in + direct pickup/food-truck** where the aggregator isn't the customer relationship.
  - **Arabic/RTL** is table-stakes for broad market; an English-only pilot works for a first design-partner but not for scale.

---

## 5. Phased Plan

**Phase 0 — Gates & foundations (BEFORE any UAE data write)**
- **Residency decision** to cai + operator (§4.4). Provision the UAE silo; pin store + project ref (LAYER-VOCAB-001). **Blocking.**
- **Payment provider partner calls** (§4.2): confirm split-settlement + small/unregistered-merchant onboarding (PayTabs primary, Stripe Connect UAE status, Telr/Tap fallback). Pick the settlement model.
- UAE **receipt profile** spec (AED, 5% VAT, TRN/unregistered modes, simplified vs full).

**Phase 1 — Dine-in QR ordering (the wedge) + KDS**
- New `tables`/QR model + signed QR tokens + table-scoped session ("order more" + open tab → settle).
- KDS as a new realtime view on the existing board; add `fired_at`/`ready_at`/`served_at` + minimal state distinction.
- UAE payment adapter (chosen provider) + receipt profile wired in.
- English-first is acceptable for a single pilot merchant; RTL groundwork started.
- **Target design partner:** a sit-down restaurant (the sushitei-shaped migration candidate) or a first Abu Dhabi merchant.

**Phase 2 — Food-truck preorder + Arabic/RTL**
- Mobile-store model (location + operating windows) on the existing pickup + `scheduled_time` flow.
- Full RTL + Arabic content across storefront/receipts (broadens beyond pilot).

**Phase 3 — Delivery + compliance follow-ons**
- `DeliveryProvider` UAE adapter (Careem Express Flash Delivery, possibly via Deliverect) for orders that need courier.
- E-invoicing/Peppol path if in-scope by the 2027 deadline.

---

## 6. Flags / Unknowns Needing a Decision or Partner Call

| # | Item | Type | Blocking? |
|---|---|---|---|
| 1 | **UAE data residency / silo choice** (PDPL extraterritorial; no managed Supabase UAE region; possible CBUAE financial-data localization). | **Residency decision (cai/operator)** | **YES — before first UAE data write** |
| 2 | **Payment provider split-settlement for small/unregistered merchants** — does PayTabs (or Telr/Tap) onboard sub-merchants; is Stripe **Connect** actually available in UAE; who is merchant-of-record + liable. | **Partner call + decision** | **YES — for real merchant payout** |
| 3 | **Which settlement model** (platform-collects-then-splits vs per-seller MoR) the abstraction implements for UAE. | Architecture decision | Yes (drives #2) |
| 4 | **Careem Express Flash Delivery** direct API access vs via Deliverect; terms/coverage. | Partner call | No (Phase 3) |
| 5 | **Jaywan** (UAE national card scheme) support requirement/timeline. | Verify | No |
| 6 | **E-invoicing / Peppol ASP** scope + first-wave applicability (pilot Jul-2026, mandatory Jan-2027). | Verify | No (design-forward now) |
| 7 | **CBUAE financial-data localization** applicability to a non-FI commerce platform. | Legal verify | Feeds #1 |
| 8 | **DIFC vs ADGM vs federal PDPL** regime for an Abu Dhabi-licensed merchant. | Legal verify | Feeds #1 |
| 9 | **Hijri date / Arabic receipt** legal requirements. | Verify | No |
| 10 | KDS status-enum change: additive `served`/timestamps vs enum rework — minimal-change decision. | Internal design | No |

---

## 7. Sources (verified 2026-07-15)

VAT / invoicing:
- https://www.cleartax.com/ae/vat-in-uae
- https://tax.gov.ae/en/taxes/Vat/vat.topics/registration.for.vat.aspx
- https://invoicedataextraction.com/blog/uae-vat-invoice-requirements
- https://www.kayrouzandassociates.com/insights/uae-tax-changes-2026-corporate-vat-fta-amendments

Payments / gateways / wallets:
- https://www.skimbox.us/en/resources/blogs/uae-payment-gateway-comparison-telr-stripe-checkout
- https://reconcileos.com/blog/payment-gateway-reconciliation-uae-guide
- https://support.stripe.com/questions/connect-availability-in-the-uae
- https://fintechnews.ae/32284/payments/digital-wallets-uae-2026/
- https://www.careem.com/en-AE/pay/

Delivery:
- https://www.gorevly.com/blog/marketing-on-talabat-vs-deliveroo-vs-careem-vs-noon-in-the-uae-food-delivery-apps-compared
- https://www.careem.com/en-AE/express/
- https://www.deliverect.com/en-ae/integrations/careem-express
- https://www.quiqup.com/post/top-delivery-companies-in-uae

Data residency / PDPL:
- https://www.kayrouzandassociates.com/insights/cross-border-data-transfers-under-uae-law-in-2026
- https://incountry.com/blog/middle-eastern-data-residency-and-compliance-details/
- https://www.dataguidance.com/jurisdictions/united-arab-emirates-federal?topic=residency

Supabase / infra region:
- https://supabase.com/docs/guides/platform/regions
- https://github.com/orgs/supabase/discussions/34551
- https://aws.amazon.com/blogs/aws/now-open-aws-region-in-the-united-arab-emirates-uae/
- https://wz-it.com/en/managed-open-source/uae/supabase/

> **Caveat:** several sources are advisory/vendor blogs, not primary regulators. Primary-source confirmation (FTA, UAE Data Office, CBUAE, and each payment provider) is required before any item marked ⚑ is treated as settled.
