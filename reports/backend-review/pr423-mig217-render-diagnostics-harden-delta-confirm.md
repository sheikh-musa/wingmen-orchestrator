# PR #423 / mig217 render_diagnostics grant-hardening — cc-quality delta-confirm

**Verdict: PASS on the migration CODE — but RESIDENCY CORRECTED: must apply to BOTH silos (goumlyne + ceayj), NOT goumlyne-only.** (propose-only; console wet-proves + applies. I did NOT apply or wet-prove — not even BEGIN..ROLLBACK. Both silos are console's touch.)

## ⚠ RESIDENCY FINDING (answers console review_request #31031 "CHECK residency — both silos, not goumlyne-only")
The migration comment + `_reserved.txt` claim **"RESIDENCY: goumlyne (irsyad) only — this table does not exist on ceayj."** This is **empirically WRONG.** Verified read-only on both silos (GOUMLYNE_RO ref `goumlyne…`, IHSANOS_PROD_RO ref `ceayj…` — distinct projects, confirmed by project-ref AND org sets: goumlyne has "Madrasah Irsyad Zuhri", ceayj has "BAPA"):

| probe | goumlyne (irsyad) | ceayj (BAPA) |
|---|---|---|
| `to_regclass('render_diagnostics')` | exists | **exists** |
| RLS enabled | true | true |
| org_admin SELECT policy | present | **present** |
| `has_table_privilege('anon',…,'SELECT'/'INSERT')` | true / true | **true / true** |
| `has_table_privilege('authenticated',…,'SELECT'/'INSERT')` | true / true | **true / true** |
| `has_table_privilege('service_role',…,'INSERT')` | true | true |

**render_diagnostics exists on ceayj (BAPA prod) with the identical un-hardened posture** the migration exists to close (anon+authenticated can SELECT and INSERT; dead org_admin read policy present). If mig217 is applied **goumlyne-only** per its stated residency, **ceayj/BAPA is left un-hardened** — anon/authenticated SELECT+INSERT on the diagnostics sink + the dead policy — the exact gap open on production. **mig217 must apply to BOTH silos.** (`role_table_grants` returned `(none)` for all roles under the auditor_ro read role — a role-visibility limitation, NOT a measurement; the `has_table_privilege` probe above is the authoritative grant check.)

**Recommend to cc-irsyad-6:** correct the residency line in the mig217 header comment + `_reserved.txt` to "both silos (goumlyne + ceayj)" so no future applier skips ceayj. Docs-coherence fix; does not block a both-silos apply. My earlier bus PASS #31032 said "residency goumlyne-only, matches mig216" — **superseded by this: both silos.** (mig216's own goumlyne-only claim is likewise contradicted by ceayj having the table; current state is what governs — harden both.)

---
**Migration CODE verdict: PASS** (propose-only; console wet-proves + applies. I did NOT apply or wet-prove — not even BEGIN..ROLLBACK. Both silos are console's touch.)
**Date:** 2026-08-22 · **Reviewer:** cc-quality (Sonnet 5, op#14199)
**PR:** https://github.com/sheikh-musa/ihsanos/pull/423 (`fix/render-diagnostics-harden-grants`)
**Thread:** 0a2149a2-5d1a-440b-a61f-6b3fdb7d022b (my ack #31016) · dispatched via cc-irsyad-coord #31026, author cc-irsyad-6 #31022 · non-urgent, no cai gate (diagnostic-infra classification, same as #422).

This is the fast-follow hardening I supplemented on #422 (render_diagnostics, mig216). Console applied mig216 + merged #422 before my grant/policy supplement, so the hardening is a standalone mig217. See `pr422-render-diagnostics-merge-crash-audit.md` (SUPPLEMENT section) + memory `new-table-default-grants-checklist`.

## Acceptance criteria (from my handoff) vs delivered

Files touched: exactly two — `supabase/migrations/217_render_diagnostics_harden_grants.sql` (+81) and `_reserved.txt` (+1). Nothing else.

1. **`REVOKE ALL ON render_diagnostics FROM anon, authenticated`** — ✅ present verbatim. `service_role` is NOT named → its INSERT path is unaffected. Verified `src/actions/render-diagnostics.ts:53` does `createServiceClient().from("render_diagnostics").insert(...)` — **insert-only, no `.select`**; the writer is untouched by the REVOKE.
2. **`DROP POLICY IF EXISTS "org_admin can read their own org's render diagnostics"`** — ✅ present. End state = service_role-only sink: RLS stays enabled, no SELECT policy = app-invisible. **No in-app reader orphaned** — the only non-migration refs to the table on the PR branch are the service-role writer (`render-diagnostics.ts`), the merge error boundary (`error.tsx`), and their tests; grep for `.select`/`.from("render_diagnostics")` read paths = zero.
3. **Nothing else rides the commit** — ✅. The only other two statements are this repo's *universal migration boilerplate*, not extra security/data DDL:
   - `COMMENT ON TABLE render_diagnostics` — metadata only (docs-coherence), no security/data effect.
   - `INSERT INTO audit_log` genesis row — present in ~20 recent merged migs (204/205/207–213/216…). **Byte-identical in shape to mig216's genesis** (`entity_type='schema'`, `action='create'`, self-referential sentinel `MIGRATION_217_GENESIS` for both prev_hash and hash). mig216 — same table, same silo, immediately prior, **already applied clean on goumlyne** (capture path live) — so the identical #217 genesis row is empirically proven against audit_log's live constraints. NB: `audit_log` is NOT the trigger-enforced hash-chain (`tg_identity_audit`, mig077); its `MIGRATION_NNN_GENESIS` rows are chain roots by convention. `-- rls-policy-exempt: audit_log update, audit_log delete` lint pragma present.
4. **BEGIN/COMMIT self-committing** — ✅ real `COMMIT` (correct for a propose-only file console applies; not a ROLLBACK stub).
5. **Collision-free number 217** — ✅ origin/main highest merged = 216 (=#422); no open PR branch claims 217+ except #423's own; `_reserved.txt` reserves 217.
6. **Residency** — ✅ goumlyne (irsyad) only, matches mig216 (table does not exist on ceayj).

## Bottom line
Delivers exactly the two security-operative statements I specified, wrapped in the repo's standard migration boilerplate; no in-app reader lost, service_role write path intact, collision-free, goumlyne-only. **CLEAR for console to wet-prove + apply on goumlyne.** No cai gate (diagnostic-infra, matches #422). I remain propose-only.
