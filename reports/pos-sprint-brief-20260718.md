# POS-improvement sprint — brief (op#5218/5220/5223 via Nazim #9712/9713/9714)

3 parallel lanes off `feat/pos-sprint` (= f724bcf, the current POS+Xendit code). Grounded in the friction findings: `reports/xendit-demo-product-review-20260718.md`.

## HARD GUARDRAILS (all lanes)
- **Monday demo is SACRED:** do NOT modify or redeploy the verified wingmen-personal PayNow-proven build (deployed from f724bcf). It stays EXACTLY as-is until after Monday. Your work lives ONLY on your lane branch → merges to `feat/pos-sprint` (NOT deployed until hub says so, post-Monday). Monday stability > new features.
- **DESIGN PIPELINE mandatory for all UI:** frontend-design pass + cc-reviewer design dimension, mobile-first, ihsan bar. Review + tests before merge.
- **No live-silo / money-gate crossings.** `next build` + typecheck + tests green. Report each piece to HUB (cc-orchestrator) as it lands; hub surfaces to Nazim (de-confliction).
- Work on YOUR branch/worktree only. Merge to `feat/pos-sprint` is hub-gated (combined cc-reviewer money+design+tests).

## ENFORCED PRODUCT PRINCIPLES (op#5223 — bake in)
1. **DYNAMIC Xendit QR = ENFORCED DEFAULT everywhere.** Strategic: routing volume through the platform+Xendit is the leverage/moat. Default every digital payment to dynamic Xendit QR (amount+ref baked, auto-confirm on webhook). Do NOT surface/promote bypass rails (static PayNow/UEN, direct-to-bank) as primary — hide/de-emphasize unless a UEN is configured. **BUT record EVERY sale in our platform regardless of tender (incl CASH)** — full GMV/volume data is leverage layer-1.
2. **CATEGORIES = CORE but FRICTIONLESS** — first-class POS-grid filter tabs; combobox on add-product; tabs scale for many-SKU. Hot path, not a settings afterthought.

## HERO FLOW (op#5220 north-star — the fastest path in the product)
Merchant charge → QR in ~2 taps: open POS/Lite → add an item OR just key an amount (quick-charge, no itemization) → DYNAMIC Xendit QR renders (amount + ref baked) → auto-confirm on webhook. Keep history/float/reconcile OUT of the hot path (they live in the owner dashboard, Track A).

---
## LANE 1 — `feat/pos-hero` (worktree ihsanos-wt/pos-hero) — HERO / fastest-QR [P0]
- **~2-tap fastest-QR path** in both full POS + Lite: open → (item OR amount-only quick-charge) → dynamic Xendit QR → auto-confirm.
- **Amount-only QUICK-CHARGE entry:** type an amount → QR (no itemization required) — the fastest path for non-itemizing merchants.
- **Enforce dynamic-Xendit-QR as the default** digital tender everywhere; **hide the static "PayNow not configured / UEN" dead tile** unless a UEN is set (steer to dynamic). De-emphasize bypass rails.
- OWNS: payment-dialog / tender hot-path / quick-charge entry / lite pay screen. Consumes the sale-record interface that LANE 2 defines (do NOT re-implement sale recording — call L2's recording with full attribution incl the payment ref). Coordinate the interface via hub if blocked.
- Design pipeline (this is the hero UI — mobile-first, ihsan bar). Tests for the quick-charge + dynamic-default logic.

## LANE 2 — `feat/pos-data` (worktree ihsanos-wt/pos-data) — TRACK A / DATA [highest value]
- **A1 — every sale airtight:** VERIFY current state FIRST (does Lite AND Full record full attribution + hash-chained audit today? where are the gaps?). Ensure EVERY sale — Lite AND Full, **including CASH and every tender** — records: counter/outlet, items, tender, payment ref, timestamp, status, + a hash-chained audit row. Close all Lite gaps. This is the GMV/volume-data leverage layer — make it airtight. OWNS the sale-record/attribution/audit data layer + defines the recording interface Lane 1 calls (publish it early so L1 isn't blocked).
- **A2 — owner dashboard:** unified transaction history across ALL counters + Lite, filterable by outlet/date. Data captured always; VISIBLE to the owner, NOT on the counter hot path. New dashboard route; design pipeline.
- Tests: attribution completeness across tenders + Lite; audit-chain integrity.

## LANE 3 — `feat/pos-onboard-cat` (worktree ihsanos-wt/pos-onboard) — FRICTIONLESS onboarding + categories
- **B1 [P0] post-onboarding dead-spot** (from review §2): a new merchant lands with NO products + NO POS-lite outlet + no token-mint UI. Fix: **auto-provision** a default outlet + a sample product on org creation + add a **"Create counter QR / link"** button in the dashboard that mints + shows the lite token (currently only `POST /api/pos-lite/sessions`, admin-API — give it a UI).
- **B2/B3 categories frictionless:** add-product category = **pick-or-type COMBOBOX** (existing categories + create-new; no free-text typos/dupes); POS-grid category **tabs scale for many-SKU** (horizontal scroll/overflow, maybe a search). Keep categories first-class in the hot path.
- OWNS: onboarding flow, products page / category UI, token-mint dashboard UI. Design pipeline (mobile-first, ihsan bar). Tests.

---
Deferred (later, not this dispatch): Track C resilience/offline (C1). Report per-piece to hub; hub runs the design+review+test gate and the merge to feat/pos-sprint.
