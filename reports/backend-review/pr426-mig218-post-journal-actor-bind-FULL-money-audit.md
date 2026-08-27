# PR #426 / mig218 — post_journal_atomic actor-bind (CAI-RESP-304 C) — cc-quality FULL money-path audit

**VERDICT: PASS** (committed-money, CAI-1170) — with a **RESIDENCY CORRECTION: apply GOUMLYNE-ONLY, not both silos.** I remain propose-only — did NOT apply or wet-prove either silo.
**Auditor:** cc-quality — model confirmed **opus-4-8** (`.quality_model`, CAI-1170 carve-out).
**Date:** 2026-08-22 · **Head:** ae4a8578 (off main incl #355/mig202) · **Dispatch:** orch-console #31172 (P2).
**Sequence (CAI-1264):** my PASS → console wet-proves BEGIN..ROLLBACK (goumlyne only, per residency below) → cai literal `execution_status='granted'` → apply + merge.

## The 4 requested verdict points — all PASS
- **(a) Guard complete + correctly placed.** `v_caller UUID := auth.uid()` in DECLARE; the mismatch/NULL RAISE is the **first executable statement** in BEGIN — before period-SELECT, before the org_admin re-check, before both INSERTs. Single entry point, no write path bypasses it. Rejects a forged actor AND a NULL uid (so an unauthenticated / service_role invocation is also denied). Mirrors mig202's void_donation guard shape exactly (the precedent I FULL-passed).
- **(b) Same signature, grant carries, no privilege change.** `CREATE OR REPLACE post_journal_atomic(JSONB, BIGINT, UUID)` — matches mig049's signature exactly (verified mig049 is the SOLE current definition on main; no later migration redefined it, so nothing is reverted). Grants restated idempotently (REVOKE ALL FROM PUBLIC + GRANT EXECUTE TO authenticated) = mig049's posture; CREATE OR REPLACE preserves existing grants regardless.
- **(c) Journal-posting body byte-identical to mig049.** Extracted both function bodies and diffed: the ONLY delta is the actor-guard (the p_actor_id comment, the `v_caller` decl, the IF-RAISE). SELECT org_id, org_admin re-check, gl_transactions INSERT, entries loop, gl_entries INSERT, RETURN — all byte-identical. Zero amount/entry/account logic drift.
- **(d) No app-code regression.** Both live callers use `createServerClient()` (session client, carries auth.uid()) and pass `p_actor_id: user.id` (post-qurban-booking.ts:108, post-qurban-slaughter.ts:106). No service-client caller of post_journal_atomic exists anywhere in src/, so the NULL-uid guard breaks nothing. **Mutation-proved** the caller-contract test: forcing a caller to pass a forged actor turned 2 tests RED.

## ⚠ RESIDENCY CORRECTION — GOUMLYNE-ONLY (console's "both silos" plan needs adjusting)
Verified read-only on both silos: **post_journal_atomic + the gl_* tables exist ONLY on goumlyne (irsyad).** On ceayj (BAPA): `fn=0`, `gl_transactions` MISSING, `gl_periods` MISSING — the qurban ledger substrate (mig049) was never applied there. Catalog-definitive, not a role-visibility limit.
- Goumlyne: post_journal_atomic exists, signature `(jsonb, bigint, uuid)`, **currently NO auth.uid bind** (`has_authuid_bind=false` — confirms the vuln is LIVE, this is a real fix), grants authenticated=true / anon=false / service_role=true.
- **Apply mig218 to goumlyne ONLY.** Applying to ceayj would CREATE an orphan function referencing non-existent gl_* tables (plpgsql doesn't validate refs at creation) — harmless but wrong; ceayj has no qurban ledger to protect. Console: wet-prove + apply goumlyne only (not both silos as #31172's sequence stated).

## Gates (at ae4a8578)
- **lint:all EXIT 0** (16/16; schema-drift 0-new with mig218; money-float clean on 169 migs). **vitest 9/9** (both qurban ledger test files). Mutation-prove passed.
- **Number 218** collision-free: main highest 216, 217=#423 (unmerged), only #426 claims 218.
- Genesis row = standard MIGRATION_218_GENESIS convention. Self-committing BEGIN/COMMIT (console strips per CAI-756).

## Findings (LOW / non-blocking)
1. **RESIDENCY** (above) — apply goumlyne-only. This is the one gate-relevant item; not a code defect, an apply-scope correction.
2. **LOW/observation** — goumlyne's post_journal_atomic carries a pre-existing `service_role` EXECUTE grant (latent; no service caller). The new NULL-uid guard already denies any service_role call (auth.uid()=NULL → 42501), so it's harmless. mig202 went further and explicitly `REVOKE EXECUTE ... FROM anon, service_role`; mig218 keeps mig049's posture. Optional: add the explicit service_role revoke to match mig202's belt-and-suspenders. Not required (the guard covers it).

**Severity concurrence:** LOW/non-urgent (org_admin-only same-org posted_by misattribution, no fund movement, no cross-org reach, no PII) — agreed; the guard additionally closes the service_role/unauthenticated forge path.

**Bottom line: PASS. Wet-prove + apply GOUMLYNE-ONLY (not both silos), then the CAI-1264 cai-grant gate.**
