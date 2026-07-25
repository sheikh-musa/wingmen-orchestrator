# Irsyad — eNETS Online Payment for Parent School-Fee Payment — Build Scope

Operator request: op#6993
Client: Madrasah Irsyad (silo: goumlyne) · Real money on client silo — cai-gated
Investigated against: origin/main @ 90a255a (ihsanos) · Date: 2026-07-25
Mode: read-only scoping. No code changed.

## 0. TL;DR verdict
- Fee VISIBILITY for parents is already BUILT end-to-end. A parent logs in, sees
  their children, and sees each child's outstanding fee invoices. What is missing
  is the PAY action and everything behind it.
- eNETS ONLINE gateway does NOT exist in the repo. "NETS" today is only a POS
  tender label (an enum value + a reference note folded into POS remarks). No API
  call, no HMAC, no redirect, no callback.
- There is NO reusable automated online-payment gateway to copy. The storefront
  "online payment" model is manual/semi-auto PayNow (customer self-attests, or
  merchant OCR-matches a screenshot, then an org_admin confirms). The webhook
  endpoint is a literal 501 placeholder. The reusable assets are the PATTERNS:
  the idempotent confirm-core + UNIQUE(idempotency_key) index + hash-chained
  audit log — but they live on pos_orders, not on the invoice tables the
  school-fee flow uses.
- Hard critical-path dependency: an eNETS ONLINE merchant agreement + credentials
  (UMID/MID, TID, Secret Key, Key ID) for Irsyad. This is a distinct NETS product
  from the NETS POS terminal Irsyad may already have. External, bank-paced,
  gates Phases 2-3.

## 1. Current school-fee state (traced e2e)

### Data model (supabase/migrations/015_school_module.sql)
- `sch_fees` = fee TEMPLATES, not per-student assignments (org_id, class_id
  nullable FK, name, amount NUMERIC(15,2), term, due_date, is_recurring). Attached
  to a CLASS, not a student; no parent link of its own.
- `sch_students`: org_id, person_id, student_number, class_id, status. Student
  identity is a `persons` row.
- `sch_student_parents`: student_id -> parent_person_id, relationship, is_primary_contact.
  This is the parent↔child edge + payer selection.

### How a fee becomes something a parent owes (src/actions/sch-fees.ts)
- `generateFeeInvoicesAction(feeId)` (org-admin/school:full) fans a fee template
  into `inv_invoices`, one per active student in the class, billed to the student's
  PRIMARY-CONTACT parent. Finds/creates an `inv_customers` row (SGD); inserts an
  `inv_invoices` row status `draft`, `source="school_fee"`, notes `[SCH_FEE:<feeId>]`.
  Idempotency is by-convention (notes-tag LIKE scan) — NO DB uniqueness guard.
  Students with no primary-contact parent are skipped.

### Parent-facing surface (EXISTS) — src/actions/sch-parent-portal.ts + src/app/dashboard/school/my/*
- Parent role real (`role="parent"`), middleware + action enforced. PDPA consent
  gate on first access. `getMyChildrenAction`, `getChildFeesAction(studentId)`
  (verifies link, returns the parent's `source="school_fee"` invoices with
  amount_due/status). UI renders Outstanding vs Paid. READ-ONLY — NO pay button.

### How an invoice gets marked paid today (src/actions/inv-payments.ts)
- `recordPayment` is `org_admin`-ONLY, MANUAL. inv_payments.payment_method CHECK =
  (bank_transfer, paynow, cash, cheque, card, other) — NO `nets`. No parent path,
  no gateway path, no idempotency key.

### EXISTS vs MISSING for "parent pays child's outstanding fees"
EXISTS: parent auth/role/consent, parent↔child link, parent-scoped invoice read,
outstanding-vs-paid UI, fee→invoice generation, manual admin payment, hash-chained audit.
MISSING (the build): online gateway (none), parent-initiated pay action, idempotency
infra on the invoice money path, `nets` method, gateway callback route, reconciliation.

### Schema-drift flags to VERIFY on goumlyne (§Phase 0)
1. `inv_invoices.source` CHECK (migration 013) = ('manual','quotation','recurring',
   'imported') — NO migration adds `'school_fee'`, yet sch-fees.ts inserts it.
   Either altered out-of-band OR fee-invoice generation FAILS the CHECK in prod.
2. Confirm whether ANY school-fee invoices exist on the Irsyad silo today.

## 2. eNETS online integration (target)
- Repo NETS = POS tender only (`src/modules/pos/api.ts`, `src/actions/pos.ts`). No gateway.
- Reusable PATTERN (model, not copy): `src/modules/storefront/payment-confirm.ts`
  (`confirmPosOrderPaymentCore` idempotent + UNIQUE(org_id,payment_idempotency_key)
  migration 061 + hash-chained audit); `src/app/api/confirm-payment/route.ts`
  (HMAC-verified inbound + rate-limit — model for the NETS S2S callback);
  `src/app/api/storefront/payment-webhook/route.ts` (501 stub documenting the shape).
- eNETS 2.0 flow: creds UMID/MID, TID, Secret Key, Key ID (per merchant/env, ONLINE
  product ≠ POS). Server builds txn request (amount, SGD, merchant ref, return+notify
  URLs) → HMAC sign (server-side only) → B2S redirect to NETS-HOSTED pages (keeps us
  out of PCI/card-data scope). Dual response: b2sTxnEnd (browser return, NEVER trust
  alone) vs s2sTxnEnd (server-to-server, authoritative — verify HMAC before crediting);
  query() to resolve ambiguity + reconcile; CRED for refunds; TEST vs LIVE envs.

## 3. Parent payment journey (target)
Parent → /dashboard/school/my → child → Fees tab (existing) → "Pay" CTA →
`initSchoolFeePaymentAction(invoiceId)` (parent-gated, re-verify ownership, guard
payable state, create pending-intent = idempotency key, HMAC-sign B2S, return redirect)
→ NETS-hosted pay → S2S callback (verify HMAC) → idempotent credit (inv_payments
method 'nets', recompute due/status) + audit → browser return shows "confirming" until
S2S lands → receipt (reuse invoice PDF/email) → invoice flips Paid.

## 4. Money-integrity + security + residency (cai sign-off)
- Idempotency: new UNIQUE(org_id, payment_idempotency_key) on the invoice payment path
  (mirror migration 061); S2S callbacks WILL replay — handler must be replay-safe.
- Callback authenticity: verify NETS HMAC on s2sTxnEnd; NEVER credit off browser
  return; query() for disputes. Model on api/confirm-payment, not the 501 stub.
- Amount integrity: credit NETS-confirmed amount vs amount_due; reject mismatches;
  block non-payable states.
- Audit: every intent/credit/refund via writeAuditLog (reuse core/audit).
- Reconciliation: scheduled query()/settlement cross-check vs inv_payments; admin
  unmatched queue.
- Residency/PII (goumlyne, ap-southeast-1): student/parent PII stays in silo; only the
  minimal txn envelope (amount, SGD, merchant ref) goes to NETS — NO student PII to NETS.
  Creds = silo-scoped server secrets (Vault/env), never code/client.
- cai gates: (a) live money writes on silo; (b) creds + residency posture; (c) refund
  authority; (d) drift fixes via guarded direct-psycopg (NEVER `supabase db push`).

## 5. Phased plan + timeline (AI-velocity)
- Phase 0 — verify/de-risk (0.5-1d): confirm source='school_fee' CHECK on goumlyne;
  confirm fee invoices exist for Irsyad; confirm eNETS ONLINE merchant agreement exists.
  Output: go/no-go + drift migration if needed.
- Phase 1 — visibility polish + payable data model (1-2d): parent portal amount_due +
  Pay CTA (stub); migration adds 'nets' method + invoice/payment idempotency-key +
  UNIQUE; fix source-CHECK drift. Ships value, no real money.
- Phase 2 — eNETS SANDBOX integration (3-5d, GATED by sandbox creds): init action +
  pending-intent + S2S callback (HMAC + rate-limit) → idempotent invoice-credit core +
  browser "confirming" + query() verify + audit + receipt. E2E on NETS UAT.
- Phase 3 — reconciliation + refunds + hardening (2-4d).
- Phase 4 — LIVE cutover (0.5-1d work; wall-clock gated by prod creds + cai).
- Engineering ~7-13 dev-days. CRITICAL-PATH WILDCARD = eNETS ONLINE merchant
  onboarding (external, bank-paced, ~2-6+ wks IF not already held; DIFFERENT product
  from POS NETS). Phases 0-1 ship immediately; P2 needs sandbox creds; P4 needs prod + cai.

## 6. Open questions (operator/client/cai)
1. eNETS ONLINE merchant account — held, or only NETS POS? (timeline driver)
2. Which fees payable online — all / current-term / partial allowed / full-only?
3. Refund policy + authority (CRED) — who, what approval, cai-gated per refund?
4. Reconciliation owner — which Irsyad role, how are mismatches actioned?
5. Schema-drift — is 'school_fee' permitted by source CHECK on goumlyne + do such
   invoices exist there today? (blocks Phase 0)
6. NETS transaction fees — parent or Madrasah absorbs, and shown on receipt?

_Scoped by cc-orchestrator (Plan agent), 2026-07-25, op#6993._
