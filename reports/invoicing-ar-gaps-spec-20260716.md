# Spec: ihsanos invoicing AR gaps — Alderei/COSEM Phase 1 (op#4644/4670)

**Owner:** ihsanos lane (build authored-unapplied) → hub review+gate → migration-apply (ceayj, guarded) + deploy → Nazim verify.
**Approved:** Nazim 9095 (hub+console consensus, op#4611/4640). Non-destructive/extend.
**Residency:** prod invoicing = ceayj (SG), verified. Migrations apply to ceayj-prod (additive/nullable/no-backfill = safe, invisible to existing org). **Keep synthetic COSEM data OUT of prod ceayj** — code + vitest only (tests mock the Supabase boundary; no live DB needed). No COSEM org creation in this task (waits on RLS-verify + cai-ratify).

## Gaps to build (5)

### 1. `expected_payment_date` (needs migration)
- **Migration** (ONE file, `supabase/migrations/0NN_invoicing_ar_gaps.sql`): `ALTER TABLE inv_invoices ADD COLUMN expected_payment_date DATE;` (nullable, no default, no backfill). Additive only — no RLS/trigger/constraint changes.
- Add to `invInvoiceSchema` (src/lib/…/schemas.ts ~L300) as optional nullable date.
- Add a date field in `invoice-form.tsx` (label e.g. "Expected payment date — when you actually expect to be paid"); thread through `createInvoice`/`updateInvoice` inserts/updates in `src/actions/inv-invoices.ts`.
- Display in the invoice list + aging view (a column/line; show only when set).

### 2. `service_tag` (needs migration)
- Same migration file: `ALTER TABLE inv_invoices ADD COLUMN service_tag TEXT;` (nullable). Invoice-level (smallest correct).
- Schema optional string; form field (free text; label "Service / course, e.g. Rota Commander Course"); thread through create/update; display as a chip on the invoice row/preview.
- Optional: a filter by service_tag on the invoices list (nice-to-have; only if cheap).

### 3. Shareable AR portfolio snapshot (FE only)
- New read-only summary view (route or a "Share / Export portfolio" action on the aging page) that reuses `getAgingReportAction` data: per-customer outstanding + aging buckets + total outstanding + (where set) expected_payment_date. Printable (print CSS) and/or CSV (CSV plumbing exists in `aging/page.tsx` `generateCsv`/`handleExport` — reuse it). A clean handoff an AR owner gives a chaser. No new data.

### 4. "Needs attention" view + large-invoice threshold (FE only)
- Extend the existing status tabs in `invoices/page.tsx` (currently all/draft/sent/paid/overdue) with a "Needs attention" tab = overdue OR (outstanding AND total ≥ a threshold). Threshold: a sensible constant (e.g. top-decile or a fixed figure) — keep it a named constant, note it's tunable. Pure client-side filter over already-fetched data.

### 5. Total Outstanding tile (FE only)
- Add a single "Total Outstanding" stat tile (= pending + overdue amount) to the invoices page stat row / landing, from existing `getInvoiceStats`. Minor.

## Conventions / gates (same as #4/#6)
- ActionResult pattern; `captureActionError` in catches; `.eq(org_id)` on every table; RLS backstop; no `any` (typed casts).
- Lint gates: check-pagination (`.limit()` after selects), check-schema-drift (**new columns must be added to any typed `.select()` that maps inv_invoices, or carry a `schema-drift-ignore` note**), check-module-boundaries (stay in invoicing module + its actions).
- **Migration discipline:** author the migration FILE only — do NOT apply it. Hub applies to ceayj at the gate via the guarded direct-psycopg `--expect-ref ceayjeamtmcyzzvqflus` pattern (NEVER `supabase db push`). No `CREATE OR REPLACE VIEW` (boot_briefing integrity). Migration must be idempotent-safe (`ADD COLUMN IF NOT EXISTS`).

## Tests + verification (before "done")
- Vitest: create/update invoice with + without expected_payment_date/service_tag (optional, persists, displays); "Needs attention" filter logic; portfolio snapshot renders from aging data; Total Outstanding = pending+overdue.
- **Regression guard:** an invoice with BOTH new fields null behaves exactly as today (additive-invisible) — assert existing create/list paths unchanged.
- Hub: eyeball the invoices + aging + snapshot render (mobile+desktop) before deploy.
- CI: unit-tests green; 0 new tsc errors in touched files; lint gates pass. lint-and-typecheck red is pre-existing.

## Out of scope (this task)
- No COSEM org creation / synthetic data in prod. No PWA (that's Phase 2, op#4670 — separate). No changes to the PDF import/extract flow, payment mechanics, or RLS.
