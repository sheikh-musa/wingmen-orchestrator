# #509 (CAI-1315 sch_students staff-branch tighten) — console floor gate

**Owner:** Nazim / orch-console · **Date:** 2026-08-25 · **PR #509** head `4cebe75a` · base=main
**Floor:** minors-PII (my gate, op#16167 — cai out of irsyad). Silos: BOTH (sch_students exists on both).

## Correctness — PASS (independent, at source)
**Live before-state (both silos, identical):** "Staff can see students in their org" [SELECT] USING `deleted_at IS NULL AND org_id IN (auth_user_staff_org_ids())` — admits org_admin/cashier/viewer/preparer (the leak). Separate unchanged policies: "org_admin full access" [ALL], "teacher select", "parent select own children". `is_student_person` absent pre-apply.

**mig254 SQL review:**
- Policy DROP+CREATE → USING `deleted_at IS NULL AND org_id IN (auth_user_org_ids_with_role('org_admin'))` = **org_admin ONLY**; teacher+parent byte-unchanged (Postgres OR-combines) → effective org_admin+teacher+parent. Shared `auth_user_staff_org_ids()` helper untouched (16-table blast radius avoided).
- `is_student_person(org,person)` — SECURITY INVOKER, STABLE, service_role-only EXECUTE; RLS-independent when called via service_role.
- `sch_students_search_ids(...)` — SECURITY INVOKER, returns **ids only (no PII)**, mirrors live filters.
- proACL (both): `REVOKE ALL FROM PUBLIC, anon, authenticated` + `GRANT EXECUTE TO service_role` (CAI-1041 ✓).

**reports.ts fail-open fix review:** OLD caller-session `.from("sch_students")` check silently fails OPEN once RLS stops admitting the role (empty≠error). NEW: `svc.rpc("is_student_person")` via `createServiceClient()`, condition `if (studentErr || isStudent !== false) → exclude`. Fail-closed: only a definitive `isStudent===false` serves; `true`/`null`/`undefined`/error all EXCLUDE. `.error` not discarded (CAI-1222 ✓).

**Matrix wet-prove — 30/30 PASS on BOTH silos (goumlyne + ceayj):** populated-PII student (medical_notes+emergency_contact) invisible to cashier/viewer/preparer; org_admin sees it PII-intact; teacher sees it; real parent sees ONLY own child (sibling negative control); cross-org=0. is_student_person: proACL anon=F/authenticated=F/service_role=T; removed roles DENIED direct call (Design-B boundary); service-role verdict TRUE for real student / FALSE for non-student (independent of caller RLS). sch_students_search_ids continuity + no false positives. Dual staff+parent sees own child via the separate parent policy (ruled-out case confirmed).

**Soft-delete edge (coord-flagged):** `is_student_person` filters `deleted_at IS NULL` → a soft-deleted student's person_id → false → history serves. Confirmed byte-identical to the OLD check (which also had `.is("deleted_at", null)`) — status-quo, NOT a #509 regression; logged follow-up ticket.

## 🔴 SEQUENCING FINDING — apply must be DEPLOY-FIRST (not migrate-first)
mig254 narrows the RLS. If applied while the OLD app code is still live in prod, the OLD `getPersonGivingHistoryAction` preparer path does a caller-session `sch_students` existence check → under the narrowed policy that returns **empty with no error** → `if (studentErr || studentRow)` is false → it **serves a minor's giving history to a preparer** = a transient minors-exclusion fail-open (the very hole the fix closes).

**Safe order (inverts the usual migrate-before-deploy for this PR):** merge #509 → new fail-closed app code live in prod on BOTH silo apps (it fail-CLOSES on the not-yet-existing RPC → over-excludes, no leak) → verify deployed → THEN I apply mig254 both silos → verify. **HOLDING my §6.6 apply until the new code is deployed.**

## Status
Correctness cleared; apply HELD pending deploy-first. Awaiting deploy confirmation, then §6.6 apply both silos + post-apply verify.
