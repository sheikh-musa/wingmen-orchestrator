# FULL audit — PR #419 CAI-1251 counter serial-scan lookup field-minimization

**Auditor:** cc-quality (FULL, decision_audit 261, no-self-merge) · **Date:** 2026-08-21 · **Verdict: PASS, merge-ready.**
**FIELD-MINIMIZATION verdict: PASS — the preparer path is STRUCTURALLY incapable of surfacing a phone.**
**APP-LAYER GATE verdict: PASS — real (not decorative), mutation-proven, independent of RLS.**
Requested by orch-console (bus #30936, thread `1394bdea`). Lens: minors-scope-and-field-minimization-fidelity. Propose-only (mig214 NOT applied — cai names the grant even on clean PASS).

Pinned HEAD `070456c4088b2887ff748af431584eb4f203ee7f` (= `gh pr view 419`, MERGEABLE, base `main`). +489/-1, mig214 (**collision-free** — 214 absent on origin/main). Gates: **lint:all EXIT 0** · **counter-lookup 9/9** · **gate mutation-proven**.

## THE CRUX (a) — field-minimization: the preparer path CANNOT surface a phone. THREE independent guarantees.
1. **Structural (SQL):** mig214's `tabung_kk_tin_lookup_by_serial_name_only` SELECTs `to_jsonb(t.*)`, student id/number, `c.name`, and **`p.display_name` only** — `persons.phone_encrypted` is **never referenced in the SELECT list** (the persons join exists solely for display_name). Confirmed in the compiled SQL, not just the return type. `to_jsonb(t.*)` is safe because `tabung_kk_tins` carries **no PII column** (verified 056 CREATE + all ALTERs: public_id/org_id/batch_id/student_id/serial_number/barcode/status/issued_by_user_id/remarks/main_tin_id — no phone/nric/email/name).
2. **Return shape:** the preparer branch constructs `person: { display_name: row.display_name, phone: null }` — phone hard-coded `null`.
3. **Code path:** `decryptPii` is **not invoked anywhere on the preparer branch**. The test's decrypt canary asserts `decryptSpy).not.toHaveBeenCalled()` for preparer (and `toHaveBeenCalledWith(...)` only for org_admin) — decrypt runs on the org_admin path ONLY.

## THE CRUX (b) — app-layer gate is REAL, independent of RLS. Mutation-proven.
`lookupTinWithStudentAction`: `getOrgContext()` → `if (ctxError || !orgId) return {…UNAUTHORIZED}` → **`if (role !== "org_admin" && role !== "preparer") return { data: null, error: null }`** — a **positive allowlist, BEFORE any DB query or RPC**. cashier/viewer/nonmember/null-role → empty, reaching **neither** the org_admin query **nor** the RPC. It gates on the role from `getOrgContext` (server session), *before* touching the DB — so it does not rely on RLS to mask the gap (the exact latent shape CAI-1251 fixes). **Mutation-proven:** perturbing the gate block makes BOTH the cashier and viewer exclusion tests go RED (they reach a path) — the gate + its tests are load-bearing, not decorative. Tests assert `res.data === null`, `rpcCalls.length === 0`, and `fromCalls` does not contain `tabung_kk_tins`.

## cai's 4 verify points
1. **App-layer gate independent of RLS, actually enforced** — ✔ (crux b, mutation-proven).
2. **Preparer path NAME ONLY, never phone** — ✔ (crux a, three guarantees; tested `person.phone === null` + decrypt-never).
3. **org_admin path UNCHANGED (full projection incl phone)** — ✔ the `role === "preparer"` branch returns early; org_admin falls through to the existing RLS-through `.from("tabung_kk_tins").select(…phone_encrypted…)` query, unchanged. Test: org_admin gets `phone === "DECRYPTED:…"`, `decryptSpy` called, `rpcCalls.length === 0`.
4. **status-drilldown untouched/still closed** — ✔ `listKkTinsByStatusAction` is **not in the diff** (out of scope, stays org_admin-only).

## Additional
- **RPC security:** `SECURITY INVOKER`, `SET search_path`, `REVOKE ALL FROM PUBLIC, anon, authenticated` + `GRANT EXECUTE TO service_role` (Design B). Org guard `t.org_id = p_org_id` (session). A browser JWT can't execute it.
- **Deploy-window fails closed:** if mig214 isn't applied (`isMissingRpc`), the preparer path captures the error and returns empty — it does **not** fall back to the wider org_admin query. Tested.
- **Decrypt canary:** `decryptSpy` proves decrypt only ever runs on the org_admin path.

## Verdict
**PASS.** Field-minimization is enforced by construction (phone_encrypted never selected + phone hard-null + decryptPii off the preparer path — three independent guarantees); the app-layer role gate is real and mutation-proven, independent of RLS (cashier/viewer/nonmember hit neither path); org_admin unchanged; status-drilldown untouched; RPC JWT-unreachable; deploy-window fail-closed. mig214 collision-free. **Propose-only** → console wet-proves + applies mig214 both silos **only on cai's named execution grant** (I do not touch goumlyne). Routing to orch-console for merge (no self-merge).
