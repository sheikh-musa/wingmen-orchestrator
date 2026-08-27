# PR #355 — committed-donation VOID/CORRECTION + reissue-race idempotency — cc-quality FULL money-path audit

**VERDICT: PASS** (committed-money, CAI-1170). Clear for console to wet-prove mig202 BEGIN..ROLLBACK both silos → apply → merge. Findings below are LOW / non-blocking (prospective hardening). I remain propose-only — did NOT apply or wet-prove.

**Auditor:** cc-quality — **model confirmed opus-4-8** (`.quality_model=claude-opus-4-8`, the CAI-1170 auditor carve-out op#14199 explicitly held for quality+storefront; session-pin beats `.fleet_model=sonnet`). Meets the CAI-1170 opus-4-8 bar for a committed-money FULL verdict.
**Date:** 2026-08-22 · **Head:** 76ecc98a (rebased on main incl #422/#425) · **Prior FULL audit:** cc-storefront @1361d62 (opus, re-audit PASS).
**Dispatch:** orch-console #31138 (P1). Scope: (a) reissue-race money-safety, (b) mig202 design-fidelity, (c) rebase-integrity of the reconstructed prior-PASS remainder.

## (a) Reissue-race idempotent guard — SOUND (CAI-1170 lens)
`reissueVoidedDonationAction` (donations.ts:1930): the link-back UPDATE is the atomic gate —
`service.update({replaced_by_donation_id: created.id}).eq(id).eq(org_id).is('replaced_by_donation_id', null).select()`. Postgres `UPDATE … WHERE … IS NULL` is an atomic compare-and-swap; exactly one concurrent caller flips NULL→created.id.
- **Won race** → 1 row returned → single live replacement.
- **Lost race** (`claimed.length===0`) → compensates by voiding the just-created row via the atomic `void_donation` RPC (soft-delete + fund_raised reversal under the same create-gate + hash-chained audit) → no double-count.
- **Compensation fails** → `captureActionError` (loud Sentry w/ createdId+voidId) + CONFLICT — never a silent double-count.
- **fund_raised across the race is correct**: A increments, B increments, B-compensation decrements → net +1 (winner only).
- **Client choices all justified**: service client for the soft-deleted void row (RLS hides it) + all service ops org-scoped (`.eq('org_id', ctx.orgId)` — no RLS-bypass leak); session client for the auth.uid()-bound compensation RPC.
- **Tests comprehensive AND mutation-proven**: WON/LOST/LOST+comp-fails/already-replaced-fast-path all covered. I disabled the lost-race compensation branch → exactly the LOST-race + comp-fails tests went RED → the guard genuinely guards (not vacuous).

## (b) mig202 design-fidelity — SOUND
`void_donation` / `unvoid_donation` / `correct_donation_meta` (SECURITY DEFINER, plpgsql):
- Actor forgery gate `p_actor_id IS DISTINCT FROM auth.uid()` → 42501 (D8). FOR UPDATE lock serializes concurrent voids. Org + org_admin re-asserted AFTER the lock, BEFORE state disclosure (SECDEF bypasses RLS — correct to re-assert manually). Reason required. TOCTOU guard on `p_expected_amount` (required in the zod schema). Receipt block (issued AND voided_at IS NULL, mig147 parity). Zakat attestation gate.
- **Immutability**: void writes ONLY deleted_at + 3 void-metadata cols; amount/person/category/donated_at/import_ref/tax-flag frozen. **fund_raised reversal** under the exact create-path gate (tabung + fund_target), NO clamp (D5), **same txn** → a later RAISE rolls back the decrement too (atomic).
- **Audit**: same-txn hash-chained append (advisory-lock + prev-hash + sha256), action=void/update. Genesis row = standard MIGRATION_202_GENESIS convention.
- **Grants (D8)**: REVOKE ALL FROM PUBLIC + REVOKE EXECUTE FROM anon, service_role + GRANT EXECUTE TO authenticated — NOT anon-reachable, service_role not extended. ✓
- **Columns**: 4 additive nullable. **Residency**: goumlyne + ceayj. **Self-committing BEGIN/COMMIT** (propose-only; console strips per CAI-756 and wet-proves).
- **Number 202**: legitimately reserved gap-fill (201→[202 reserved for #355]→203…216). Verified order-independent: **no migration 203–216 references 202's objects** (void_donation/voided_at/replaced_by_donation_id/correct_donation_meta) and 202 depends only on pre-existing tables → applies cleanly on prod (name-tracked psycopg) and on a fresh rebuild. Not a renumber.

## (c) Rebase-integrity — CONFIRMED clean
- #355's NET contribution to donations.ts (`origin/main...head`) = exactly the 6 money-action functions appended after main's `suggestDonorForDonationAction`, + 1 blank line at 1370.
- **All 6 money-action functions byte-identical** between prior-audited 1361d62 and head (voidDonationAction, mapDonationVoidError, unvoidDonationAction, correctDonationMetaAction, voidAndReissueDonationAction, reissueVoidedDonationAction) — zero logic drift from the reconstruction. Main's relink/suggest/receipt code untouched by #355.
- **mig080 modification = comment-only** — corrects a factually-wrong money-precision note ("donations has a status column" → it has none; void = soft-delete via deleted_at, mig202) + documents that existing deleted_at filters already exclude voided donations. Zero executable-SQL change; `BEGIN;` unchanged. check-schema-drift passes (tooling-safe).

## Gates (at 76ecc98a)
- **lint:all EXIT 0** — all 16 substrate gates (schema-drift 0 new despite the mig080 edit; money-float clean on 168 migs incl mig202; rls-invariant; bigserial; supabase-select; …).
- **vitest 27/27** (donations-void-correction incl. 4 race tests + architecture).
- **mutation-prove**: lost-race compensation disabled → 2 race tests RED → restored.

## Findings — all LOW / non-blocking (prospective hardening)
1. **LOW — discarded `writeAuditLog` error** at reissueVoidedDonationAction:1987 (CAI-1222 smell; my `fail-closed-verify-callee-contract` rule). Fails open on audit failure — BUT it's a *supplementary* reissue-linkage note: the durable linkage lives in `replaced_by_donation_id` (set atomically), the void is atomically audited by the RPC, and `createDonation` doesn't audit_log-track creation at all in this system. So money integrity + linkage are independently protected (CAI-1250 principle). Recommend: check the return + surface/reconcile on failure.
2. **LOW — reissue's corrected `donated_at`** (D2 period-material date) is patched via service client post-create and captured in NO audit payload (the linkage audit records amount+category only). Provenance gap, not a money double-count/loss. Recommend adding donated_at to the reissue audit payload.
3. **LOW/theoretical — void→unvoid fund_raised asymmetry**: if a category's fund_target is nulled between a void and its unvoid, the re-increment gate is false while the decrement gate was true → fund_raised under-counted by the amount. Extreme mid-cycle-reconfiguration edge; consistent with the deliberate D5 "surface drift, no clamp" design. Awareness only.

**Bottom line: PASS. mig202 clear to wet-prove (both silos) + apply + merge; the 3 LOW items are prospective hardening, none block.**
