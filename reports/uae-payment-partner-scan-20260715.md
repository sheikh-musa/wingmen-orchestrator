# UAE Payment-Partner Scan — Embedded Rails for the Wingmen Marketplace (Abu Dhabi/UAE)

**Date:** 2026-07-15
**Author:** cc-orchestrator (research fan-out, 4 parallel agents, ~68 web sources)
**Mirrors:** the prior Singapore scan that picked HitPay (merchant-collection) + Stripe (platform billing).
**Question:** which UAE PSP do we ride as the merchant-collection adapter behind our `PaymentProvider` interface, so the platform books the payment, funds split to each small merchant, and we AVOID fund-custody / merchant-of-record liability (no heavy CBUAE licence on us)?

---

## TL;DR

1. **The SG "unregistered individual" play does NOT port cleanly to the UAE.** UAE law requires a trade licence to sell — including home businesses selling over Instagram/WhatsApp. There is **no compliant "fully unregistered merchant" path with ANY provider.** The realistic model is **platform-as-licensed-marketplace**: we ride a CBUAE-licensed PSP's split-settlement rail, and each merchant holds at minimum a **cheap licence-light permit** (freelancer / e-trader / home-food permit, AED 200–1,800/yr). Build a licence + food-safety verification gate into onboarding.
2. **"Custody = licence" is the same fault line as Singapore's PS Act.** If the PSP split-settles directly to each merchant and we only take our commission via their split, we plausibly avoid our own CBUAE licence. If we ever pool funds and redistribute, that IS "Payment Aggregation Services" and needs a CBUAE PSP licence (AED 2–10m capital + AML program). **Architect so the PSP custodies and splits; we never touch funds.** Needs UAE payments counsel sign-off on the money-flow.
3. **Best first partner: Tap Payments.** Cleanest documented Stripe-Connect-style destination-charge / sub-merchant model, CBUAE full payment-services licence (incl. aggregation), broadest confirmed rails (cards + Apple/Google Pay + **Careem Pay** + Tabby + Tamara), most transparent SME-friendly pricing, best developer experience.
4. **Runner-up: Checkout.com.** The only provider that publicly productizes the exact platform-as-MoR / sub-entity-onboarding / split-settlement model AND holds a CBUAE **acquiring + aggregation** licence, with a live UAE PayFac proof point (Mamo runs on it). But it positions upmarket (wants ~AED 500k+/mo volume) — heavier for a marketplace of tiny F&B sellers.
5. **The unregistered-merchant tension, stated honestly:** the split-capable rails (Tap, Checkout, Telr, Stripe) all require each sub-merchant to be *some* licensed entity. The ONLY no-trade-licence onboarding path found (Ziina, CBUAE SVF) has **no marketplace/split** — so with Ziina we'd custody-and-redisburse ourselves (undesirable). No provider gives us both "no merchant licence" AND "split without custody."

---

## Comparison table (provider × 6 dimensions)

Legend: ✅ verified positive · ❌ verified negative · ⚠️ partial/conditional · ❓ unverified — needs partner call

| Provider | 1. Small/unlicensed onboarding | 2. Marketplace split (platform not custodying) | 3. Rails (cards/wallets/BNPL) | 4. Fees & settlement | 5. CBUAE-licensed | 6. Integration |
|---|---|---|---|---|---|---|
| **Tap Payments** | ⚠️ business licence for KYC; freelance-permit path ❓ | ✅ **Tap Connect / Marketplaces** — `destinations` object, per-seller KYC + payout (Stripe-Connect-style); MoR/custody posture ❓ | ✅ broadest: Visa/MC/Mada/KNET/Benefit, Apple Pay, **Google Pay**, **Careem Pay confirmed**, Tabby+Tamara | ✅ transparent: **2.75%** cards, no monthly; T+1 (marketplace T+5) | ✅ **full CBUAE payment-services licence (2025), incl. aggregation** | ✅ strongest dev experience; Marketplace/Business API, webhooks, sandbox, SDKs |
| **Checkout.com** | ⚠️ platform-is-MoR "Regulated Platform"; each sub-merchant needs "all necessary licenses"; unregistered path ❓ (underwriting call) | ✅ **Platforms product** — sub-entity onboarding + KYC + split + local-currency payout; platform is MoR, CKO isn't; UAE proof: **Mamo** | ✅ Visa/MC + regional, Apple/Google Pay, **Tabby (direct partnership)** + Tamara; Jaywan ❓ | ❓ no public rate; interchange++ negotiated; wants ~AED 500k+/mo; T+1–T+3, T+0 available | ✅ **first global platform with CBUAE acquiring + aggregation licence (May 2023)**; direct acquirer | ✅ modern REST, settlement webhooks, free sandbox |
| **PayTabs** | ✅ **accepts UAE freelance permit** in lieu of trade licence (most freelancer-friendly direct onboarding) | ⚠️ split/multi-vendor + escrow (SwitchOn payfac stack); MoR/no-custody terms ❓ | ✅ Visa/MC, Apple/Google Pay, Mada/KNET/STC/SADAD, Tabby+Tamara; Careem Pay/Jaywan ❓ | ⚠️ ~2.85% + AED 1 (down to 1.75% by volume); plans AED 0–183/mo; T+3–T+5 | ❓ **CBUAE licence NOT confirmed** (Saudi-HQ; material gap for ride-the-licence) | ✅ mature REST, hosted+S2S, webhooks, sandbox, SwitchOn stack |
| **Telr** | ❌ **trade licence mandatory**; sub-merchants must be Sole Est./FZE/LLC — **no freelance-permit sub-sellers** | ✅ **Telr Split Payments** (2023) via API; but AED-only, UAE-bank-only, beneficiaries need trade licence; T+≤7d | ⚠️ Visa/MC + **Tabby**; Apple/Google Pay ❓; Tamara ❓; **online Jaywan announced (Apr 2026)** | ⚠️ plans AED 99–349/mo + 2.49% + AED 0.50; T+1–T+3 | ✅ **CBUAE Retail Payment Services Licence (20 Mar 2025)**, incl. aggregation | ✅ good docs, unified REST, sub-merchant onboarding API |
| **Stripe (UAE)** | ❌ **trade licence required; individuals explicitly NOT supported** | ✅ **Connect IS live for UAE platforms** (Custom accounts, destination/separate charges) — BUT each connected account needs its own UAE trade licence; no platform-liability accounts; `on_behalf_of` blocked | ⚠️ Visa/MC, Apple/Google Pay, Link only — **NO BNPL, no Mada/Jaywan/local wallets** | ✅ 2.9% + AED 1 (+1% intl, +1% FX); Connect add-ons ~$2/acct/mo (❓); T+2–5 | ⚠️ acquirer, launched UAE 2025; merchant is own MoR; exact CBUAE authorisation ❓ | ✅ best-in-class API, Connect onboarding UIs, webhooks, sandbox |
| **Ziina** | ✅ **Professional account — NO trade licence to start** (best light-onboarding); CBUAE SVF-backed | ❌ **no marketplace/split evidenced** — single-merchant collection tool; platform would custody & re-disburse itself | ⚠️ Visa/MC/Amex, Apple/Google Pay, QR/NFC/tap-to-pay; **no BNPL**; Jaywan ❓ | ✅ 2.6% + AED 1 (+1.5% intl); **instant settlement** | ✅ **CBUAE Stored Value Facility (SVF) licence** | ⚠️ REST Payment Intent API, links, OAuth, plugins; webhooks/sandbox ❓ |
| **Mamo** | ❌ **trade licence + MOA required** (serves "registered" freelancers/businesses) | ⚠️ split/bulk **payouts** exist, but reads as custody-then-disburse, not sub-merchant acquiring; runs ON Checkout.com | ✅ Visa/MC/Amex, Apple/Google Pay, **Tabby**; Tamara ❓ | ✅ 2.7% (1.9% w/ card spend) + AED 0.80; Tabby 6.9%+AED1; T+1 | ❌ **DFSA (DIFC), NOT CBUAE** — onshore authorisation gap | ✅ strong REST, webhooks, dedicated sandbox |
| **Network Int'l (N-Genius)** | ⚠️ trade licence / e-trader permit effectively mandatory; no public unregistered or platform-sub-seller path | ❓ **no public marketplace/split/PayFac product** (docs index shows none); bespoke enterprise underwriting only | ✅ Visa/MC/Amex/UnionPay, Apple/Google/Samsung Pay, **Jaywan confirmed**, Aani, Tabby+Tamara | ❓ bespoke; benchmark ~2.2–2.9% + AED 1; T+1–T+3 | ✅ dominant CBUAE-regulated acquirer (>50% UAE card volume); ride-the-licence for sub-merchants ❓ | ✅ mature REST, webhooks, sandbox — **but no split/sub-merchant primitives** |
| **Magnati** | — **MERGED into Network Int'l (7 Oct 2025)** — treat as N-Genius; do not evaluate as a separate counterparty | (as N-Genius) | Apple/Samsung/Google Pay + payit; Tabby/Tamara/Jaywan ❓ | ❓ bespoke; indicative CNP ~2.5–3.8% | ✅ CBUAE acquirer (by merger approval) | ⚠️ N-Genius is the stronger surface |
| **Amazon Payment Services (ex-Payfort)** | ⚠️ "legal setup" required; no public freelancer/unregistered path | ❓ **no public marketplace/split product**; "split payments" it markets = Tamara BNPL, NOT multi-merchant settlement | ✅ Visa/MC/Amex/UnionPay, Apple Pay, Mada/KNET/Meeza, Tabby+Tamara; Google Pay/Jaywan ❓ | ⚠️ ~AED 200/mo + 2.80% + AED 1; T+1–T+2 | ⚠️ CBUAE RPS licence (Oct 2023); sub-category (acquiring vs aggregation) ❓ | ✅ REST API, docs, plugins; legacy Payfort endpoints live; sandbox depth ❓ |
| **Tabby (BNPL rail)** | ⚠️ must be **legally registered + business bank account**; accepts freelancer/e-commerce licence; not fully unregistered | n/a (a rail, not a marketplace host) — embeds inside PayTabs, Checkout.com, APS, Tap, Geidea | BNPL installments | MDR ~2.5–3.5% + ~AED 1 (❓ third-party) | ✅ **CBUAE SVF/wallet licence (16 Apr 2026)** | ✅ SDKs/plugins + first-class inside major PSPs |
| **Tamara (BNPL rail)** | ⚠️ business docs + UAE settlement bank expected; not fully unregistered | n/a — embeds via Geidea and other gateways | BNPL installments | MDR ~2.99–3.99% + per-order (❓ third-party) | ✅ **CBUAE Restricted Finance Company licence (20 Oct 2025)** | ✅ REST (Merchant ID/key/token), Shopify, Geidea |

---

## The load-bearing regulatory reality (why UAE ≠ Singapore)

**Trade licence is legally required to sell — including home businesses.** An individual cannot lawfully sell (even via Instagram/WhatsApp) without one. VERIFIED across multiple UAE legal/business-setup sources. Licence-light routes a home business CAN use:

- **Freelancer / sole-professional permit** (free zones, DDA, ADRA, MOHRE): ~AED 1,800 licence-only; named individual only; does not itself cover food prep.
- **Tajer Abu Dhabi licence** (TAMM/ADRA): home-based, AED 1,000–3,000/yr — **but food prep/catering excluded / needs extra approval.**
- **Home-food licences (F&B-specific):** Dubai Home Food Processing licence ~AED 200/yr (+ Municipality food-safety approval + hygiene course); Abu Dhabi home-food licence ~AED 800/2yr (+ **ADAFSA** approval + training).
- **CRITICAL eligibility caveat:** home-kitchen food licences are **restricted to Emiratis / GCC nationals. Non-GCC expats CANNOT get home-kitchen approval** — they must use a **cloud kitchen** under a proper economic/free-zone trade licence. This materially bounds the F&B merchant base we can legally onboard.

**Aggregator / sub-merchant model (CBUAE RPSCS, Circular 15/2021):** a licensed aggregator/PSP CAN onboard sub-merchants under a master account (sub-merchant onboarding ~24–48h vs 2–4 weeks for a dedicated MID). But: (a) sub-merchants are "customers" subject to risk-based KYC — SDD/CDD/EDD tiers exist, but **no confirmed bright-line AED threshold** for automatic lighter KYC (UNVERIFIED — legal counsel item); (b) the marketplace must **contract with each sub-merchant + disclose principals** to the acquirer (per Checkout.com's UAE sub-merchant terms).

**What licence WE need:** the HitPay-analog IS available — ride a CBUAE-licensed PSP's marketplace/split product and never custody funds. The decisive line is **fund custody**: if the PSP splits and settles directly to each sub-merchant and we only take commission via their split, we plausibly avoid our own CBUAE licence. If we ever pool funds in our own account then redistribute, that IS Payment Aggregation Services → our own CBUAE PSP licence (AED 2–10m capital + AML). General principle VERIFIED; **precise application to our fund-flow design UNVERIFIED — must be reviewed by UAE payments counsel.** New in-scope entities under Federal Decree-Law No. 6 of 2025 must regularise by **16 Sep 2026.**

---

## Ranked recommendation (priorities: a=small/licence-light merchants, b=split w/o custody, c=cards+wallets+BNPL, d=CBUAE-licensed, e=terms)

### 1st — **Tap Payments** (best first partner)
Cleanest documented Stripe-Connect-style destination-charge / sub-merchant model (b), CBUAE full payment-services licence including aggregation (d), broadest confirmed rails incl. Careem Pay + Tabby + Tamara (c), most transparent SME-friendly pricing with no monthly fee (e), best developer experience. Weakness: the freelance-permit onboarding path for the smallest sellers is unconfirmed (a) — top partner-call item.

### 2nd — **Checkout.com** (structural front-runner on paper, enterprise-tilted)
The only provider that publicly productizes platform-as-MoR + sub-entity onboarding + split settlement (b), holds a CBUAE **acquiring + aggregation** licence (d), with a live UAE PayFac proof point (Mamo runs on it). Carries Tabby + Apple/Google Pay (c). Weaknesses: positions upmarket (wants ~AED 500k+/mo volume, opaque negotiated pricing) — heavier for tiny F&B (a, e); each sub-merchant still needs a licence; sub-merchant terms opt out of UAE Consumer Protection Regulation (legal review for a consumer F&B marketplace).

### Wildcards / situational
- **PayTabs** — the most **freelancer-permit-friendly direct onboarding** (a) and flexible split, but its **CBUAE licence is UNVERIFIED**, which is a blocker for the ride-the-licence strategy. Verify licence first; if confirmed, it jumps up for licence-light merchant onboarding.
- **Ziina** — the ONLY verified **no-trade-licence onboarding** (CBUAE SVF), useful as a low-friction rail for the very smallest sellers, but **no split** → we'd custody & re-disburse (triggers our own licence question). Not a marketplace rail; possible fallback for solo sellers only.
- **Telr** — CBUAE-licensed with a real split product, but strictest on (a): mandatory trade licence, sub-merchants must be establishments/LLCs, AED-only/UAE-bank-only. Weak for tiny sellers.

### Rule out (for this shape)
- **Stripe UAE** — Connect is live but individuals explicitly unsupported + **no BNPL/local wallets**; wrong fit for licence-light F&B. (Still a candidate for platform billing, the SG-Stripe role.)
- **Network Int'l / Magnati (one entity now)** and **Amazon Payment Services** — strong single-merchant acquirers with **no public marketplace/sub-merchant/split product** and no unregistered path; only viable via a bespoke enterprise deal that public evidence does not confirm exists.
- **Mamo** — DFSA (DIFC) not CBUAE, trade licence required, payout-not-acquiring split.

**Suggested next step:** partner calls with **Tap (lead)** and **Checkout.com (parallel)**, plus a **PayTabs CBUAE-licence verification**. Take a UAE payments-counsel read on the fund-flow (custody line) and on whether "platform holds a master licence, sellers are sub-merchants on cheap permits" is contractually + regulatorily clean.

---

## What could NOT be verified from public sources (needs a partner/compliance call)

**Critical (dims 1 & 2):**
1. **Can sub-sellers onboard without their OWN trade licence when the platform is licence-holder/MoR?** — the whole ballgame; NO provider answers this publicly. Ask Tap, Checkout, PayTabs directly.
2. **Exact MoR / funds-custody posture** per provider's split product — who custodies, who bears chargeback/AML liability. (Tap, Checkout, Telr, PayTabs all have split "capability" verified; the no-custody legal posture is unverified for each.)
3. **Tap:** is there a freelance-permit onboarding path for the smallest sellers?
4. **Checkout.com:** minimum viable KYC for an unregistered UAE home/F&B sub-merchant (Emirates ID + IBAN, no trade licence?); UAE region-enablement of the Platforms product; a small-merchant pricing program.
5. **PayTabs:** **CBUAE licence status** (unconfirmed — decisive for ride-the-licence).
6. **Ziina:** any marketplace/split/sub-account product at all; whether its SVF licence lets a PLATFORM collect and pay out to unlicensed sellers on its own licence.

**Secondary:** platform/wholesale pricing tiers (all quote merchant retail only); Jaywan (UAE domestic scheme) support across most providers; BNPL rate cards (Tabby/Tamara third-party estimates only); whether a numeric small-merchant KYC threshold exists (likely no bright line — risk-based SDD/CDD/EDD); Careem Pay / e& money / Payit availability as discrete checkout rails per PSP.

**Legal (counsel, not vendor):** whether our aggregation of licence-light sub-merchants triggers our OWN CBUAE PSP/aggregator licensing under RPSCS + Decree-Law 6/2025; sign-off that the PSP-split-settles-we-never-custody design keeps us out of scope.

---

## Key sources
- CBUAE RPSCS Rulebook — rulebook.centralbank.ae/en/rulebook/retail-payment-services-and-card-schemes-regulation
- CBUAE CDD/KYC Rulebook — rulebook.centralbank.ae/en/rulebook/3-customer-due-diligence
- Checkout.com CBUAE acquiring licence — checkout.com/newsroom/checkout-com-becomes-the-first-global-payments-platform-to-secure-acquiring-license-from-the-uae-central-bank; sub-merchant terms — checkout.com/legal/sub-merchant-terms; Platforms — checkout.com/docs/platforms
- Tap Payments marketplaces — tap.company/en-us/products/marketplaces; dev docs — developers.tap.company/docs/marketplace-getting-started; CBUAE licence — gulfnews.com (Tap full payment services licence 2025)
- Stripe Connect UAE availability — support.stripe.com/questions/connect-availability-in-the-uae
- Ziina CBUAE SVF — thefintechtimes.com/ziina-secures-stored-value-facility-licence-from-uae-central-bank...; no-licence onboarding — ziina.com/help-center/6847490
- PayTabs freelancer onboarding — ai.paytabs.com/en/freelancer-certificate-permit-accepted-get-started-with-paytabs
- Telr CBUAE RPS licence — electronicpaymentsinternational.com/news/telr-retail-payment-licence-uae; split — docs.telr.com/reference/split-payment
- Mamo on Checkout.com / DFSA — checkout.com/newsroom (Mamo); mamopay.com/legal/terms-business
- Network Int'l × Magnati merger — gulfnews.com/business/banking/network-international-magnati-officially-merge-1.500290283
- Amazon Payment Services (alive/MENA) — paymentservices.amazon.com
- Tabby CBUAE SVF/wallet licence — tabby.ai/en-AE/newsroom/wallet-licence; who-can-apply — tabby.ai/en-AE/help-business/applying-to-tabby/who-can-apply
- Tamara CBUAE restricted finance licence — thepaypers.com/fintech/news/tamara-secures-a-restricted-finance-license-from-the-cbuae
- Home/freelance licences — adra.gov.ae/en/establishing/tajer-abu-dhabi-licence; fragomen.com (UAE freelance licence 2025); safeledger.ae/blog/home-made-food-license-uae-requirements
