# mig 106+107 → goumlyne parity package (MONEY migration, cai-gated)

**From:** cc-orchestrator (hub) · **Authority:** CAI-RESP-471 (107→goumlyne PRE-CLEARED, strict money gate, expedited) + operator #5083 · **Date:** 2026-07-17
**Repo:** ihsanos · **Worktree:** `~/wingmen/ihsanos-wt/mig107-goumlyne` (branch `chore/mig107-goumlyne-parity`, off origin/main 14f5684). **AUTHORED package only — do NOT apply.** The HUB applies the guarded migration to goumlyne after cai flips the grant.

## Why
goumlyne (irsyad's dedicated silo) lagged ceayj: delete-before-sign (107) + invoicing AR-gaps (106) never landed there, so `was_ever_signed` etc. are missing and Elly's delete-before-sign ERRORS on live. Root defect = MIGRATION-PARITY-1. Close the drift by applying 106 + 107 to goumlyne.

## The package (build these; hub reviews + sends to cai + applies)
1. **Migrations to apply (already exist on main, applied to ceayj):**
   - `supabase/migrations/106_invoicing_ar_gaps.sql` (adds `inv_invoices.expected_payment_date` + AR cols)
   - `supabase/migrations/107_tabung_weekly_report_delete_before_sign.sql` (adds `was_ever_signed`/`delete_reason`/`deleted_by` + `idx_tabung_weekly_reports_deleted_by` + fn `tabung_report_was_ever_signed_monotonic` + trigger + **its self-contained backfill** `UPDATE tabung_weekly_reports SET was_ever_signed=true WHERE preparer_signed_at IS NOT NULL`)
2. **Verify idempotency against goumlyne's ACTUAL current schema** (goumlyne HAS `deleted_at`, is MISSING the rest — confirmed). Both migrations are `IF NOT EXISTS`/additive; confirm they apply clean on goumlyne with no destructive step, no RLS change, no content change.
3. **CRITICAL — the was_ever_signed BACKFILL (cai's non-negotiable condition):** 107's backfill MUST latch goumlyne's 6 ever-signed reports true. cai verified: of the 7 goumlyne reports, SIX are ever-signed (ids **5,10,31,56,57,64**) and only ONE is a genuine unsigned draft (id **63**, `preparer_signed_at IS NULL`). Confirm the backfill (`WHERE preparer_signed_at IS NOT NULL`) sets exactly those 6 true and leaves only 63 false. If ANY of the 6 would be missed (e.g. a reopened report with cleared preparer_signed_at), STOP and flag — without the backfill the delete-gate would allow deleting 6 REAL signed reports (REPORT-IMMUT-1 breach).
4. **Guarded apply SCRIPT (author it, do NOT run):** direct-psycopg dry-run→apply targeting **goumlyne** (ref `goumlynecruxrlmzlntp`), pattern of `scripts/apply_*.py` (PR #41/#42/#44) — `--expect-ref goumlynecruxrlmzlntp` fail-closed ref check, single transaction, pre/post verification, **NEVER `supabase db push`** (decision-962). It should: verify ref → dry-run (BEGIN…ROLLBACK, report what would change) → on go, apply (single txn) → post-proof.
5. **Post-proof assertions (in the script):** all columns/idx/fn present on goumlyne; monotonic-fn/trigger works (true→false raises); the 6 ever-signed reports (5,10,31,56,57,64) = `was_ever_signed true`; id 63 = false; ZERO change to any report's contents/totals/`content_hash`; row counts unchanged.

## Report back to cc-orchestrator
Branch + SHA; the guarded apply script path; the DRY-RUN output against goumlyne (what it would change, incl the backfill row-set = exactly ids 5,10,31,56,57,64); confirmation of idempotency + no-content-change; and the post-proof plan. AUTHORED only — hub runs a cc-reviewer goumlyne-target pass, sends the package to cai for the grant flip, and applies. Money migration on a minors-PII client silo — precision over speed; the backfill is the whole point.
