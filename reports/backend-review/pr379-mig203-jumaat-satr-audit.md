# cc-quality FULL ship-audit — PR #379 + mig203 (Tabung Jumaat Phase-A, Satr-focus)

- **PR:** #379 `feat(tabung): Tabung Jumaat tin-by-tin dashboard [op#14950 Phase A]`
- **Migration:** `supabase/migrations/203_tabung_jumaat_stats.sql` (propose-only, gates the apply)
- **Reviewer:** cc-quality (Opus 4.8), 2026-08-20 · reviewed at source (`gh pr diff 379`)
- **Requester:** orch-console (bus #29051), assigned as primary FULL auditor, independent of the builder (cc-irsyad) and C2 reviewer (cc-irsyad-coord)
- **Gate:** AUDIT-BEFORE-APPLY — this report gates cai's §6.6 apply of mig203, not just the PR merge

## Verdict: **PASS.** Safe to apply mig203 and merge #379.

## PRIMARY FOCUS — SATR/minors-PII exclusion (verified independently, not taken on trust)

`jumaat_top_holders` carries a structural `NOT EXISTS (SELECT 1 FROM sch_students ss WHERE ss.person_id = t.person_id AND ss.org_id = p_org_id)` anti-join. I traced this against the actual persons-RLS carve-out at source (`supabase/migrations/192_persons_student_carveout.sql`, CAI-RESP-1030 F2) rather than trusting the migration comment's self-description:

- mig192's authoritative exclusion set is `auth_nonadmin_staff_student_person_ids()`: `SELECT ss.person_id FROM sch_students ss WHERE ss.org_id IN (...)`. **No `deleted_at` filter on either side** — a soft-deleted student row still excludes the person, and mig203's NOT EXISTS matches that (also no `deleted_at` filter). No drift.
- mig203's exclusion is scoped to `ss.org_id = p_org_id` (the single verified session org the RPC is called with); mig192's is scoped to the caller's role-based org set. These are the correct analogues for a single-org RPC vs. a cross-org RLS policy — equivalent for the call pattern actually used here.
- Confirmed `umum_top_donors` (082) genuinely has **no** such exclusion — the claimed bug-class match is real, not asserted. This exact gap is a **live, HIGH-severity, currently-open incident** on the Umum side (CAI-RESP-1192, ~890 real children on this org, interim-fixed by PR #376 merged 2026-08-20 00:08Z, ~22 min before this PR was opened). mig203's structural, RPC-level fix is the durable pattern the fleet is moving toward, not the interim admin-gate Umum got.

**Independent live wet-prove (BEGIN...ROLLBACK, goumlyne prod, org 73339164 Madrasah Irsyad — the real target tenant), reproducing and cross-checking cc-irsyad's own claimed "10/10" wet-prove rather than accepting it on trust:**

1. Inserted a synthetic person + a synthetic banked jumaat tin ($105.50) held by them. Ran the exact `jumaat_top_holders` query body inline (migration not yet applied, so no live function to call) → holder appears, `tin_count=1`, `total_amount=105.50`. Ran `jumaat_status_counts` body inline → `banked=1`.
2. Linked the **same** person to a `sch_students` row in the same org (made them a minor).
3. Re-ran `jumaat_top_holders` → **0 rows** — the holder is fully excluded.
4. Re-ran `jumaat_status_counts` → **still `banked=1`**, unaffected — counts carry no PII and correctly don't exclude.
5. Cross-check: confirmed the synthetic person is in the exact same set (`EXISTS (SELECT 1 FROM sch_students ss WHERE ss.person_id = ...)`) that mig192's carve-out helper would also hide from non-admins → the two exclusions can't drift apart because they key off the identical fact (a `sch_students` row exists for this person in this org).
6. `ROLLBACK`, then independently verified **zero residue** — no synthetic person/tin/student row persisted in prod.

Before running any INSERT against prod I checked for triggers on the 4 touched tables (`organizations`, `persons`, `tabung_umum_tins`, `sch_students`) — all are `BEFORE UPDATE` only (timestamp/lock/bank guards), none fire on INSERT, none have external side effects. Safe.

**This independently confirms the primary Satr gate is genuinely closed, not merely well-documented.**

## Secondary checks (also PASS)

- **`jumaat_status_counts` soundness:** verbatim `umum_status_counts` (082) body + `tin_type='jumaat'` filter only. No exclusion needed or present (counts only, no names — correct).
- **Page role-gate** (`jumaat/page.tsx`): `resolveActiveOrgContext` (CAI-734, session-verified, not the grandfathered `.limit(1).single()` pattern) — fail-closed (`redirect` to `/login` on `UNAUTHORIZED`, `/dashboard` otherwise), explicit allow-list to the 4 intended roles (`org_admin`/`cashier`/`viewer`/`preparer`), `notFound()` for anyone else.
- **Nav entry:** role set matches the existing peer entries (Umum/Keluarga); correctly de-duplicated the label collision — new entry "Tabung Jumaat" → `/dashboard/tabung/jumaat`, pre-existing entry relabeled "Jumaat Reports" → `/jumaat-reports`, no functional change to the latter's target.
- **Issue-dialog tin_type lock:** `const [tinType] = useState<TabungUmumTinType>("jumaat")` — no setter destructured, hard-locked; this screen structurally cannot issue a non-jumaat tin (no Umum-collection pollution).
- **`getJumaatTopHoldersAction`:** `orgId` derived from `getOrgContext()` → `supabase.auth.getUser()`, a genuine server-verified session — never user/URL input, matching the migration's cross-org-guard claim. Graceful degrade via the pre-existing `isMissingRpc` helper (scoped to `PGRST202`/`42883`/specific message patterns, not a blanket catch) if mig203 isn't applied yet — empty panel, not a 500. Display-name resolution for ranked holders goes through the **RLS-scoped** `persons` client (not service-role) as a second, independent defense layer: even in a hypothetical RPC-exclusion bug, a student's row would still be hidden from non-admin roles by mig192's own RLS, degrading to `"(unknown holder)"` rather than leaking a name.
- **Grants/security shape:** `REVOKE ALL ... FROM PUBLIC, anon, authenticated` + `GRANT EXECUTE ... TO service_role` on both functions (Design B, matches 082/142/mig200 precedent); `SECURITY INVOKER` correct (service-role caller already bypasses RLS — `DEFINER` would only widen blast radius for no benefit). Audit-log genesis row present and documents the minors-exclusion rationale.

## Non-blocking observations

- `getJumaatTopHoldersAction` relies on the page's role-gate rather than independently re-checking role server-side — but this mirrors the pre-existing `getUmumTopDonorsAction` convention (not a regression introduced by this PR), and the RPC itself is now safe for every in-org role by construction, so there's no PII-exposure risk in that gap.
- CI (lint/typecheck/tabung-correctness/tabung-synthtest/unit-tests) was still QUEUED/PENDING at audit time — the studio self-hosted runners went offline ~00:31Z (bus #29058-29060), an unrelated, already-tracked infra issue, not mine to resolve. `irsyad-frs` E2E and both Vercel previews were already SUCCESS. Migration apply is a separate propose→console-apply path independent of CI; full PR merge should still wait on CI-green per the standing safe_merge gate.

## Bottom line

mig203 is safe to apply (additive, read-only, correctly scoped, structurally minors-safe, independently wet-proven by me against prod). PR #379 is sound. On apply + CI-green → safe_merge, no further condition from me.
