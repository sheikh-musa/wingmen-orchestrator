# PR#678 + PR#679 — fundraising_admin committed-donations SELECT + bank-payer display — FULL-tier apply-gate review

**Auditor:** cc-quality (Opus 4.8, CAI-1170 money/minors floor). **Date:** 2026-09-10. **Silo:** goumlyne (live irsyad). **Repo:** sheikh-musa/ihsanos.
**PRs:** #678 head `08f7fa82` (mig327 + donations.ts + page.tsx + test), #679 head `0d4eca38` (donations-list.tsx display). **VERDICT: PASS — clear to apply/merge/deploy.** Independent adversarial pass; do-not-apply respected (verdict routed to orch-console).

## Method
Read the committed PR content at head (not a local checkout); live-RO on goumlyne (auditor_ro) for policy/lockstep/non-vacuity; ran the load-bearing minors tests against the real `listDonations` at PR head. No mutation, no apply.

## The 5 asks — all confirmed
**1. Minors wet-prove, NON-VACUOUS.** Verified via the combination of live-RO enumeration + source + the real-code unit test (stronger than a single app-run):
- Non-vacuity LIVE: org `73339164` has **206 student-linked donations, all 206 carrying a raw payer name** (`reference_no`/`counterparty_raw`) — the work order's "206/206". (Two other orgs: 0 student-linked.)
- Withholding is source-proven in `src/modules/donations/api.ts` (the real `listDonations`): for `!hasMinorsScope` callers, any returned row with `person_id ∈ studentIds` gets `reference_no = null; counterparty_raw = null` (≈line 350); and a payer-name search pre-resolves matches then **in-memory anti-joins out every student-linked id** (never a search match), with a belt-and-suspenders `personIds` filter against RLS drift and an impossible-id pin to avoid the empty-`.or()` matches-everything trap.
- `studentIds` fetched **fail-closed**: `merge_org_student_person_ids` RPC error → `listDonations` **THROWS** ("refusing to serve donations without minors-exclusion") — never falls open.
- Executable proof: `list-donations-fundraising-admin-minors-exclusion.test.ts` + the sibling reconcile test — **12/12 pass** against the real `listDonations` at PR head. The test imports the real accessor (stubs only the Supabase client), uses a synthetic payer name, and carries a non-vacuous **org_admin NEG-CONTROL** (same student row's payer fields present + search-matched — proves the assertions aren't vacuous) plus a fail-closed RPC-error throw case.

**2. App-gate fail-closed (no accidental org_admin minors scope).** `hasMinorsScope = viewerRole === "org_admin"` is the SOLE determinant (api.ts:204). `getDonationsOverrideOrgContext` admits `fundraising_admin` but returns `role: base.role` unchanged (donations.ts:111); `listDonationsAction` passes `viewerRole: role` (the real membership role, :344); `page.tsx` adds fundraising_admin only to the page-access gate. So fundraising_admin → `viewerRole !== 'org_admin'` → `hasMinorsScope=false` → the floor fires. No path elevates it. Undefined role also fails closed (`=== "org_admin"` is false).

**3. INSERT/UPDATE untouched; write & privileged surfaces org_admin-only.** mig327 DROP/CREATEs only the SELECT policy; INSERT/UPDATE policies untouched. `createDonationAction` excludes fundraising_admin (donations.ts:141). Adversarial extension — the raw-CSV **export** (`exportDonationsAction`, emits `d.reference_no` raw) and **reports** are `role !== "org_admin"` → FORBIDDEN (donations.ts:638/729), so fundraising_admin cannot bypass the withholding via export. `isAdminEquivalent` = org_admin/subadmin only; fundraising_admin is a separate list-view branch, so every `isAdminEquivalent`-gated action excludes it.

**4. PR#679 display inherits withholding by construction.** `BankPayer` reads `d.counterparty_raw?.trim()` straight off the same `listDonations` row (the field #677 already NULLs for non-org_admin student rows); renders `null` when empty; no second data path, no service-role read, `person_id` untouched. Correct by construction.

**5. Lockstep + no-drift + fail-open hunt — clean.**
- **No policy drift (live):** the live donations SELECT policy's 3 branches (staff+org_admin-softdelete-carveout, subadmin, surface-override) match mig327's reproduction exactly; only the 4th `fundraising_admin` (non-deleted) branch is new. Verified against the LIVE `pg_policy`, not the migration file's self-claim.
- **mig291 lockstep (live):** `fundraising_admin` is in BOTH the persons SELECT outer array AND the inner `auth_nonadmin_staff_student_person_ids()` fn (which returns `sch_students.person_id` for fundraising_admin orgs). The dangerous asymmetry (outer-without-inner → students leak) is NOT present. So a student donor's `display_name` is blanked for fundraising_admin at the persons keystone.

## Observation (not a blocker)
There are **0 `fundraising_admin` members on goumlyne today**, so mig327's grant is **additive-but-dormant** (grants to a role no one holds → nothing exposed today; safety verified prospectively). This is safe because the app-layer floor is **structural** — it fires on `viewerRole !== 'org_admin'`, independent of whether any fundraising_admin currently exists — so it holds the moment the first fundraising_admin member is created. No standing "safe only while 0 exist" hazard.

## Verdict
**PASS.** Defense-in-depth minors floor is intact for fundraising_admin: RLS row-grant (SELECT-only, non-deleted) + persons-keystone display_name blanking (mig291 lockstep, live) + app-layer `reference_no`/`counterparty_raw` withholding & search-exclusion (#677, fail-closed) + export/write org_admin-only. PR#679 inherits by construction. No drift, no lockstep asymmetry, no fail-open path found. Clear to apply.

*Scope note: I ran the load-bearing minors-exclusion tests, not the full `lint:all`/CI suite — the console's apply gate covers full CI; my PASS is the minors/money-floor adversarial verdict.*
