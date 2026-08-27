# FULL audit — PR #415 CAI-1249 counter student-name lookup gate (minors-PII, RLS-bypass RPC)

**Auditor:** cc-quality (FULL, no-self-merge) · **Date:** 2026-08-21 · **Verdict: PASS, merge-ready.**
Requested by orch-console (bus #30774, thread `6ebd3edd`). Lens: minors-scope-fidelity. Propose-only (mig211 NOT applied/wet-proven — console owns goumlyne apply). Verified at source + tests + mutation.

Pinned HEAD `fd8f163cd74c089753468e0cdb8241eea6c30aa6` (= `gh pr view 415`, MERGEABLE, base `main`).
7 files: mig211 + `tabung-keluarga.ts` + client + 4 tests. Gates: **lint:all EXIT 0** · **25/25 (3 touched test files)** · **gate mutation-proven**.

## 1. ROLE GATE (CAI-1249 hard condition) — CONFIRMED, mutation-proven.
Both gated actions in `tabung-keluarga.ts` derive `role` server-side (`getOrgContext` → `resolveActiveOrgContext(supabase)` = the authenticated session, never client input) and gate **before** the service-role RPC:
- `lookupTinsByStudentNameAction` (916): line 940 `if (role !== "org_admin" && role !== "preparer") return { data: { results: [], capped: false }, error: null }` — **then** line 952 `createServiceClient().rpc("tabung_kk_student_name_lookup", { p_org_id: orgId, … })`.
- `lookupStudentsForTinAssignmentAction` (1008): line 1026 same gate → `{ results: [] }` — **then** line 1035 `createServiceClient().rpc("sch_students_typeahead", …)`.
cashier/viewer → **EMPTY (not error, not leak)**; the RPC is never reached. Not relying on RLS — the RPC bypasses RLS via the service-role client, so the app-layer gate is the whole control, and it's by-construction (a leak can't happen regardless of what mig192/069/085 admit). **Mutation-proven:** deleting the role check in `lookupTinsByStudentNameAction` turns BOTH the cashier and viewer exclusion tests RED (`2 failed / 8 passed`) — they'd otherwise reach the RPC.

## 2. TESTS PROVE EXCLUSION (not assert-non-empty) — CONFIRMED.
The mock records every `.rpc(fn, params)` into `rpcCalls`. For both actions: `it.each(["cashier","viewer"])` asserts `res.data` is empty **AND `rpcCalls.toHaveLength(0)`** (the service-role RPC is genuinely never invoked — gate is real, not cosmetic); `it.each(["org_admin","preparer"])` asserts `rpcCalls.toHaveLength(1)` + full name returned. name-lookup also asserts `decryptSpy` not called. 25/25 green on head.

## 3. MIGRATION 211 mirrors mig196 EXACTLY — CONFIRMED.
Compared to `196_tabung_kk_tins_student_label_rpc.sql`: both `LANGUAGE sql STABLE SECURITY INVOKER SET search_path = public, pg_temp`, `REVOKE ALL … FROM PUBLIC, anon, authenticated` + `GRANT EXECUTE … TO service_role` (Design B, 142/082). mig211's two RPCs use **narrow projections** — tin fields + `student_number` + `class_name` + `display_name` (and tin ids); **no money/amount column** selected (identical scope to the actions they replace). Org guard `org_id = p_org_id` from the verified session (never user input). Read-only, `BEGIN/COMMIT` (CAI-756-safe), audit genesis row, cites CAI-RESP-1249. Both RPCs return FULL `display_name` and are gated org_admin/preparer-only — appropriately **stricter** than mig196's cashier/viewer-reachable first-name render surface, because these are active search/enumeration surfaces (a first-name prefix search still enumerates the roster) — matches the CAI-1249 ruling.

## 4. BYPRODUCT leak-closure (reduces, not adds) — CONFIRMED.
Before #415, `lookupTinsByStudentNameAction` had **no role check** and the counter route is reachable by cashier (nav-legit) AND viewer (nav-hidden but page-reachable) → both got FULL student names. After #415 they get **EMPTY** → exposure **reduced**. The tin-assignment typeahead's client swaps `getSchStudentsAction({search,per_page:10})` → the new gated `lookupStudentsForTinAssignmentAction(needle)` (keluarga-admin-client.tsx). **`sch-students.ts` (the shared `getSchStudentsAction`) is NOT in the diff** — its other caller (`school/students/page.tsx`) is untouched (no collateral behaviour change).

## Non-blocking coherence flag (routed — does NOT block #415)
origin/main tops out at migration **209**; **211 is collision-free on main** ✓. But **two unmerged branches both claim mig210**: `feat/cai1209-delivered-webhook` (#402, the Resend delivered-proof webhook I cleared) and the parked CAI-1247 branch (per this migration's own header). Latent cross-branch collision — whichever merges second must renumber. #415 itself correctly used 211 to avoid stacking on its author's own 210. Worth a merge-ordering note to whoever sequences #402 / CAI-1247.

## Verdict
**PASS.** The load-bearing minors-PII control — an app-layer server-side role gate (org_admin/preparer) checked BEFORE the RLS-bypassing service-role RPC — is present in both actions, returns empty (not leak) for cashier/viewer, is proven real by RPC-never-called tests and a gate-removal mutation, and mig211 mirrors the ratified mig196 Design-B pattern with narrow no-money projections. #415 reduces (never adds) exposure; shared action untouched. Propose-only → console wet-proves + applies mig211 (CAI-756-safe, correct silos); I verified the migration by inspection + comparison to the applied mig196 (I do not touch goumlyne). Routing to orch-console for merge (no self-merge). Flagged the latent mig210 cross-branch collision as a separate coordination item.
