# PR#681 — TIN-receipt amount derive-at-display — FULL-tier money-path review

**Auditor:** cc-quality (Opus 4.8, CAI-1170 money-path). **Date:** 2026-09-10. **Repo:** sheikh-musa/ihsanos, head `12d47eb4`. **Silo:** goumlyne. **Apply DEFERRED.**
**VERDICT: PASS on the derive CORRECTNESS; one ask-#1 completeness FINDING (a missed export surface).** Independent adversarial pass; do-not-apply respected (verdict → orch-console; console is self-recycling — next console body reconciles at boot).

## Correctness — VERIFIED (PASS)
- **Helper (ask 2):** `deriveReceiptDisplayAmount({tinUmumId, storedAmount, tinAmountTotal})` = `tinUmumId ? Number(tinAmountTotal ?? 0) : Number(storedAmount)`. Pure, no side effects; TIN → counted total (0 if uncounted), DONATION → stored UNCHANGED. Matches #631's derive-at-display pattern and the receipt-PDF's `Number(tin.amount_total ?? 0)`.
- **Live regression (ask 3), byte-exact on goumlyne:** 703 tin receipts — **687 unchanged**, **16 zero→positive summing to exactly $9,438.75**, **would_change_a_positive = 0**, **derive_lowers_amount = 0**. The derive never changes a positive receipt and never lowers an amount; the only changes are the 16 stale-$0 corrections. Matches the coord's numbers exactly.
- **Minors (ask 4):** the derive reads ONLY the `amount_total` scalar, added to the **existing** `source_tin` embed (already used by `deriveTinSource`) — no new collector/person_id join, no new identity serialization. Umum student-collector identity is untouched.
- **Immutability (ask 5):** derive-at-read only; no migration in the PR, no `UPDATE receipts.amount` — the immutable stored value is never mutated.
- **CI:** ran the PR test — **9/9 pass**; `lint:all` — **17/17 green**.

## Render-surface enumeration (ask 1) — independently re-enumerated from source
Verified each surface that renders/exports a tin-receipt amount:
- **LIST** (`receipts/api.ts`) — FIXED (derives `r.amount`). ✓
- **LETTER-PDF** (`api/receipts/[id]/letter/pdf/route.tsx`) — FIXED (calls the helper). ✓
- **receipt-PDF** (`api/receipts/[id]/pdf/route.tsx`) — already derives (`Number(tin.amount_total ?? 0)`, op#17667). ✓
- **EMAIL** (`actions/receipt-email.tsx`) — a tin receipt (donation_id null) fails the donation fetch → NOT_FOUND bail before any amount render; the `tabungFallbackAmount` path is for tabung-*category donations* (donation_id set), unaffected. ✓
- **GROUPED-PDF** (`api/receipts/grouped/route.tsx`) — `.not("donation_id","is",null)` explicitly excludes tin receipts. ✓
- **VOID dialog** (`receipts/void-receipt-dialog.tsx`) — renders `receipt.amount`, but `setVoidTarget(r)` feeds it the **derived LIST row**, so it inherits the derive by construction (not a fresh stored read). ✓
- **Mosque consolidated email** (`actions/masjid-collection-email.tsx`) — already derives from `amount_total` (`tinAmount(tin.amount_total)`), not stored. ✓
- **Receipt-CREATE audit payload** (`actions/receipts.ts:144`) — audit trail of the issued DONATION receipt's stored amount; not a tin-display surface. ✓

### FINDING F1 [MEDIUM, ask 1] — the enumeration is INCOMPLETE: the receipts CSV data-export reads stored amount for tin receipts
`src/actions/data-export.ts` → `exportReceiptsAction()` selects the raw `amount` column for **every** receipt in the org (`.eq("org_id", orgId)`, no tin/donation filter, no derive) and writes `receipts_<org>_<date>.csv`. So the 16 tin receipts export their **stale $0** — the exact $9,438.75 understatement the PR fixes for the display surfaces PERSISTS in the CSV export. This surface is **not** in the coord's enumeration, and ask #1 explicitly names "CSV export" as an in-scope surface. Independent completeness pass caught it (the coord sampled the display paths).

**Design nuance:** a raw table-dump export can defensibly ship the stored column, but here `stored` is *known-wrong* for tin receipts, so a reconciliation/accounting export that reports $0 for real money is a hazard. Recommend the team decide: derive the amount in the receipts CSV too, OR export **both** the stored `amount` and the counted `amount_total` (so the export is honest about the snapshot-vs-counted distinction). Org_admin-gated + a secondary surface, so MEDIUM not HIGH; does not block the display fix. Apply is deferred → time to address.

## Verdict
The derive-at-display fix is CORRECT, pure, #631-consistent, minors-safe, immutable, and 0-regression (verified live, byte-exact) — **PASS on the fix itself**. But the ask-#1 completeness claim ("enumeration complete") is **not** met: `exportReceiptsAction` (receipts CSV) still reads the stored $0 for tin receipts. Recommend closing F1 (derive or dual-column in the CSV export) before this is called "all surfaces fixed." Advisory / alert-not-block per charter — the fix/accept call is the operator's & console's.

*Method: authoritative source at PR head 12d47eb4 (fetched to a temp ref, throwaway worktree); full render-surface re-enumeration from source (not the coord's list); live goumlyne RO regression check (703/687/16/$9,438.75, 0 would-change-positive); ran the PR test (9/9) + lint:all (17/17). No mutation, no apply.*
