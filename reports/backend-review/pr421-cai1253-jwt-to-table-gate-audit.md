# FULL audit — PR #421 CAI-1253 flat-worklist JWT-gate → table-gate (Design B revert)

**Auditor:** cc-quality (FULL, decision_audit 263, no-self-merge) · **Date:** 2026-08-21 · **Verdict: PASS, merge-ready. No security regression.**
**BOTH-RPCs verdict: BOTH fully migrated — NOT a half-migration.**
Requested by orch-console (bus #30976, thread `0f256159`). Lens: no-security-regression-jwt-to-table-gate. Root cause of the empty-panel bug (refresh-lagged JWT false-denied a legit preparer). Propose-only (mig215 NOT applied — cai names the grant).

Pinned HEAD `85efce5bf76a4b200cb08cf9106a8521aa5c22b9` (= `gh pr view 421`, MERGEABLE, base `main`). +279/-34, mig215 (**collision-free** — 215 absent on origin/main). Gates: **lint:all EXIT 0** · **18/18**.

## BOTH-RPCs check (the critical consistency check) — BOTH migrated. CONFIRMED, all three legs.
| | `tabung_unreturned_kk_tins` (LIST) | `tabung_unreturned_kk_tins_count` (COUNT) |
|---|---|---|
| JWT gate dropped from body | ✔ (`auth_user_org_ids_with_roles` appears only in header comments, not in either function body) | ✔ |
| EXECUTE → service_role-only | ✔ `REVOKE ALL FROM PUBLIC, anon, authenticated, service_role` + `GRANT ... TO service_role` (reverts mig212's `TO authenticated`) | ✔ |
| Action calls via `createServiceClient` | ✔ `const svc = createServiceClient(); svc.rpc("tabung_unreturned_kk_tins", …)` | ✔ `svc.rpc("tabung_unreturned_kk_tins_count", …)` |

Not a half-migration: the exact failure mode called out (COUNT left JWT-gated → `createServiceClient` has no `auth.uid()` → count returns 0 → same bug half-fixed) does **not** occur — the COUNT RPC's JWT gate is removed and it too is called via the service-role client.

## cai's 3 verify points
1. **App-layer TABLE gate equivalent-or-STRONGER than the dropped JWT gate — no path where the app-gate passes for someone the table should deny. CONFIRMED.** The app-layer gate (`role !== "org_admin" && role !== "preparer" → FORBIDDEN`) is fed by `getOrgContext → resolveActiveOrgContext → org_members` — i.e. it **is** a direct read of the authoritative table, so it cannot pass for a role the table denies. The dropped in-function gate was **AND-ed** with this one (mig212 required table-pass AND jwt-pass). Dropping the AND-term changes the admitted set from `{table-pass ∧ jwt-pass}` to `{table-pass}`; the newly-admitted delta is exactly `{table-pass ∧ jwt-FAIL}` = legitimate users the *stale* JWT wrongly denied (Elly's case). No illegitimate user is newly admitted — a JWT claim is a lagged copy of the table and can only be **more** stale, never fresher, so the second gate could only ever produce false *denials*, never false *grants*. Table gate ≥ combined security.
2. **Design B matches mig196/211/213/214 EXACTLY. CONFIRMED.** `SECURITY INVOKER`, `SET search_path`, `REVOKE … + GRANT EXECUTE TO service_role` only, org guard `org_id = p_org_id` from the verified session, role gate in the calling action.
3. **No regression for cashier/viewer exclusion. CONFIRMED.** The app-layer gate FORBIDs cashier/viewer (table-based); the RPCs are service_role-only EXECUTE (a browser JWT can't call them directly). Tests: cashier/viewer FORBIDDEN + RPC-never-reached; org_admin/preparer reach it. 18/18.

## Also confirmed
- **SQL / anti-join byte-identical to mig212 (beyond the gate).** The LIST RPC's JOINs + `WHERE t.org_id=p_org_id AND t.status='issued' AND t.deleted_at IS NULL AND NOT EXISTS(missing-reports reported/written_off) AND (serial/student_number/name/class ILIKE)` + `ORDER BY t.issued_at ASC NULLS LAST, t.id ASC LIMIT/OFFSET` match mig212 line-for-line; the **only** removed line is `AND p_org_id IN (SELECT auth_user_org_ids_with_roles(...))`. COUNT is the symmetric same-minus-gate.
- **Session-client mock has no `.rpc` — a regression fails LOUD.** The test moved the RPC mock to a separate `makeServiceMock()` (for `createServiceClient`); the session mock (`makeSupabaseMock`) deliberately has **no `.rpc`**, so any code path that wrongly called the RPC pair via the session client throws a `TypeError` (loud), not a silent wrong-answer. A half-migration would fail the suite.
- **writeAuditLog untouched.** Not in the #421 diff — the #416 `unreturned_worklist_view` best-effort log is unchanged.
- **mig215 collision-free** (215 absent on origin/main).

## Verdict
**PASS — no security regression.** Both RPCs fully migrated to Design B (JWT gate removed from both bodies, both service_role-only EXECUTE, both called via `createServiceClient`); the app-layer table gate is equivalent-or-stronger (it *is* the authoritative table read — dropping the AND-ed stale-JWT duplicate removes false-denials only, never adds a false-grant); Design B matches the audited siblings; cashier/viewer exclusion holds; anti-join byte-identical to mig212; the session-mock guards a half-migration loudly; audit untouched. **Propose-only** → console wet-proves + applies mig215 both silos (goumlyne+ceayj) **only on cai's named execution grant** (I do not touch goumlyne). Routing to orch-console for merge (no self-merge).
