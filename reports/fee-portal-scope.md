# Madrasah Irsyad — School-Fee Portal — Scope & Design (Fable, 2026-08-05)

Produced by a Fable-model agent per operator op#10510 ("get fable to scope it"), grounded in the actual sheikh-musa/ihsanos source. Hub-verified additions: **residency = Irsyad org is in goumlyne (goumlynecruxrlmzlntp), confirmed at source (org present in goumlyne, absent in ceayj) — TENANT-RESIDENCY gate CLEAR.** Bug-1 CHECK-constraint corroborated at source on BOTH silos.

## Headline corrections to the earlier assumption
- The fee LEDGER is **NOT a gap** — `inv_invoices` + `inv_payments` + `recordPayment`/`deletePayment` (src/actions/inv-payments.ts) already exist and are money-safe (per-invoice partial payments, fail-closed on silent RLS drops, audit-logged).
- What's actually broken (Phase-0, small):
  - **Bug 1 (root cause, unfixed):** `inv_invoices` source CHECK = ('manual','quotation','recurring','imported') — rejects `generateFeeInvoicesAction`'s `source='school_fee'` (Postgres 23514). Fix = one additive migration adding 'school_fee'. HUB-CONFIRMED both silos.
  - **Bug 3 (new finding):** parent RLS lockout — inv_invoices/inv_customers grant SELECT only to org_admin; no parent-role policy → parents silently see nothing. Fix = parent SELECT policies mirroring migration 015's proven JOIN pattern.
  - **Bug 2 (open Q):** sch-import-parents.ts exists (remediation) but confirm it was RUN against the ~892 roster.
- **No live automated payment capture exists** — the storefront payment webhook is a 501 STUB. "Online payment" today = PayNow QR (locked amount) + manual/OCR-assisted confirm. No live card gateway (only a Stripe CSV parser for donation imports).
- **eGIRO = fully greenfield.** Critical unknown: OCBC e-DDA = real-time API or batch file (SFTP/ISO20022)? Decides the whole architecture. No cron/scheduled-job primitive exists in the codebase (needed for recurring generation + any GIRO batch).

## The real new build
- A **payment-session / allocation layer**: settle N invoices (across children) atomically off one payment event — models `confirmPosOrderPaymentCore` (guarded UPDATE, 0-rows→fail-closed, unique idempotency index, hash-chained audit). ~150-250 loc. HIGHEST-RISK piece → dedicated cai design review.
- **Per-student ad-hoc fee assignment** (excursions): sch_fees only supports blanket-class today; need a target list (new `sch_fee_assignments`).
- Parent cart/checkout UI + `getMyFamilyFeesAction` (cross-child).

## Phasing / effort
- **Phase 0 (S):** fix CHECK + add parent RLS + confirm parent-import → unblocks the already-built-but-broken parent Fees view. Low-risk, high-leverage.
- **Phase 1 (M):** payment-session multi-invoice allocation core + family fees + cart/checkout + PayNow QR + admin confirm queue. The crux.
- **Phase 2 (S-M):** per-student ad-hoc fees.
- **Phase 3 (L, floor):** eGIRO — mandate model/signup/collection/reconciliation/reversal. Do NOT size until OCBC API-vs-file confirmed.
- **Phase 4 (opt S-M):** real gateway for true zero-touch capture.

## Money-safety gates (for cai design review)
Atomic multi-invoice settlement; silent-RLS-drop fail-closed idiom (house style — keep it); real UNIQUE(org_id, idempotency_key) index; parent RLS correctness (reuse mig-015 pattern); residency (goumlyne — HUB-CONFIRMED); GIRO reversal/clawback audit-distinct path; don't oversell "online payment"; OCBC unknowns.
- **PostgREST max_rows=1000 silent truncation (from substrate audit #6 / CAI-RESP-734):** any list query returning >1000 rows silently truncates (a raw `.limit(2000)` returns only 1000, no error) — money-adjacent lists (fees/invoices/payments across a school of ~892 students × multiple fees) WILL exceed 1000. Use SECURITY DEFINER RPC or explicit server-side pagination, never a raw `.limit(>1000)`. (The concrete #6 instance — tabung-keluarga.ts:1225 missing-tin dashboard — is cc-irsyad's fix, folded into the #10 build per Nazim's spec 15739; noted here so the fee/payment/ledger lists carry the same discipline.)

## Open questions for operator/client
1. OCBC e-DDA: real-time API or batch file? (ask OCBC — sizes Phase 3)
2. Zero-touch instant payment (needs a real gateway, Phase 4) vs "PayNow QR + school confirm" for v1?
3. Was sch-import-parents run against the real ~892 roster?
4. Per-student ad-hoc fees confirmed (vs blanket-class)?
5. Any existing cron/scheduled-job primitive? (Fable found none — infra to add.)
6. GIRO settlement UEN = the org's existing paynow-identity/organizations.uen, confirmed for Irsyad?

Lanes: fee-ledger + school = cc-irsyad (Irsyad/school); checkout/payment/eGIRO = storefront/platform (cc-ihsanos), on the Gazzabyte COMMERCIAL key footing (CAI-729/730), not the Syed token.
