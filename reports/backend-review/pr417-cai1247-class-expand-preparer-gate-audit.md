# FULL audit — PR #417 CAI-1247 class-expand drill-down preparer minors-gate

**Auditor:** cc-quality (FULL, no-self-merge) · **Date:** 2026-08-21
**FAIL-CLOSED verdict: PASS (mutation-proven).** Overall: **CONDITIONAL PASS — item 4 (audit logging) is a GAP, coord's call whether it blocks.**
Requested by orch-console (bus #30840, thread `2ccdabdf`). Client-blocking P1. Design B (service-role RPC + app-layer gate). Propose-only (console wet-proves + applies). Verified at source + tests + mutation.

Pinned HEAD `09d4318f7fb11b38d6cbb5651bb76db85f7d7066` (base includes #416/mig212). Migration **213** (collision-free — after 211/#415, 212/#416). Gates: **lint:all EXIT 0** · **drill-down tests 12/12** · **fail-closed gate mutation-proven**.

## 1. FAIL-CLOSED app-layer gate — PASS (mutation-proven). ★ the security boundary
Both actions (`getKkStudentsByClassAction` returned-side, `getKkUnreturnedStudentsByClassAction` still-out-side) have the identical shape:
```
const { orgId, role, error: ctxError } = await getOrgContext();
if (ctxError || !orgId) return { data: null, error: {…UNAUTHORIZED} };   // context error → deny, before RPC
if (role !== "org_admin" && role !== "preparer") return { data: [], error: null };  // POSITIVE allowlist → empty, before RPC
const svc = createServiceClient();
const { data, error: rpcErr } = await svc.rpc("tabung_kk_(unreturned_)students_by_class", {…});
```
- **Positive allowlist** (must be *exactly* org_admin or preparer) — cashier, viewer, **and any null/undefined/unexpected role fall to empty**, never to the service-role RPC. This is the correct fail-closed shape (allowlist, not denylist).
- **`.error` is checked, not discarded** — `getOrgContext` RETURNS `{error}` and the action gates on `ctxError` (not the #406 fail-open shape). A throw anywhere is caught by the outer `try/catch` → INTERNAL_ERROR (no names).
- **Mutation-proven:** weakening the gate to a denylist (`role === "cashier"` only — fails open for viewer/null) turns the "viewer gets an EMPTY result, RPC never reached" test **RED**. The gate + its test are real.

## 2. Cashier/viewer VERIFIED empty (not error, not leak) — PASS.
The expand handler has no UI role gate (coord-confirmed → cashier/viewer DO reach the action). Tests: `it.each(["cashier","viewer"])` → `res.data === []` **and RPC never called** (empty, not FORBIDDEN, not a leak); `it.each(["org_admin","preparer"])` → RPC reached (the P1 fix). 12/12.

## 3. RPC unreachable by a browser JWT — PASS.
mig213: **both** RPCs `REVOKE ALL … FROM PUBLIC, anon, authenticated` + `GRANT EXECUTE … TO service_role`. An authenticated/anon browser JWT calling `/rpc/tabung_kk_students_by_class` (or the unreturned one) → permission denied. SECURITY INVOKER, called only via the action's service-role client (Design B, mirrors 142/196/211).

## 4. Audit logging — **GAP (item NOT met).** ⚠ flagged under my CAI-1250 forward-check
**Neither class-expand action writes an audit log.** The only `writeAuditLog` sites in `tabung-keluarga.ts` are the *write* actions (createBatch/updateBatchPrintedCount/recordReturn/…); both class-expand **read** actions resolve minor `display_name` via the service-role RPC with **no logging**, and the parent `getKkClassCompletionAction` resolves no names — so this drill-down's minor-name resolution is **entirely unlogged**. This is **inconsistent with #416** (the flat worklist `listUnreturnedKkTinsAction` logs every name-resolution view) and **below the CAI-1250 baseline** (best-effort logging on minors-PII name-resolution, with bounded-window alerting + reconciliation). The message listed "Audit logging present" as an expectation — it is not present.
- **Severity:** accountability, not access-control (the fail-closed role gate independently protects the data — no leak). Per CAI-1250 this is the *best-effort* case, so it need not be fail-closed — but it must **exist**. Absence of any logging is below baseline.
- **Recommendation:** add best-effort `writeAuditLog` (mirroring #416's `unreturned_worklist_view` shape — actor/role/class/result_count) to both class-expand actions, with the CAI-1250 alert+reconcile posture. Whether this blocks the P1 merge or lands as a committed, tracked fast-follow is coord's call, given the client-blocking urgency + the fail-closed gate already preventing any leak. My advisory: it should not merge *silently* without either the log added or an explicit tracked follow-up, because it's a minors-PII name-resolution surface and CAI-1250 now governs.

## 5. Aggregation byte-identical to the replaced JS — PASS.
- Returned side: `COUNT(*)::INT` (= `tin_count += 1`), `SUM(COALESCE(t.amount_total,0))::NUMERIC(15,2)` (= `+= Number(amount_total ?? 0)`, NULL→0 no poisoning), `status IN ('returned','counted','banked','closed')` (= non-'issued' `.in([...])`), `ORDER BY total_amount DESC`. Active-batch CTE (is_active + latest created_at) = old batch resolution.
- Still-out side: `COUNT(*)::INT`, `COALESCE(array_agg(serial_number ORDER BY serial_number) FILTER (WHERE serial_number IS NOT NULL), '{}')` (= only non-null serials pushed, empty-array default), `status='issued'`, `ORDER BY student_number`. The two sides partition the class total (issued vs non-issued) with no overlap.

## 6. Second RPC (returned-side) got the identical fix — PASS.
`getKkStudentsByClassAction` has the byte-identical fail-closed gate + Design-B service-role call as the still-out side (verified above); mig213's `tabung_kk_students_by_class` mirrors the unreturned RPC's security/grant model.

## Verdict
**FAIL-CLOSED verdict: PASS, mutation-proven** — the app-layer allowlist gate is the security boundary and it fails closed (context error/throw/uncertain-role → empty/deny, never the service-role RPC); cashier/viewer empty-not-leak; RPCs JWT-unreachable; aggregation byte-identical; both sides fixed. **The one open item is #4 — audit logging is absent** on both name-resolution actions (below the CAI-1250 baseline, inconsistent with #416). Recommend adding best-effort audit (+ alert/reconcile) before merge, or as an explicitly tracked fast-follow — coord/cai's blocking call, since it is accountability (not access-control) and the P1 is client-blocking with the leak already closed by the fail-closed gate. Propose-only → console wet-proves + §6.6-applies mig213 both silos after PASS + CI green. Routing to orch-console (no self-merge).

---

## DELTA re-verify — audit-add head `09d4318` → `813709f` — bus #30864, thread `ff428217`
The item-4 audit gap was **closed in-PR** (coord ruling). Delta = `tabung-keluarga.ts` + the 2 drill-down tests; **mig213 byte-unchanged** → the mutation-proven fail-closed security verdict at `09d4318` stands. **CONDITIONAL → full PASS.** lint:all EXIT 0; drill-down tests 20/20 (12 + 8 new audit tests); my best-effort proof 4/4.

My 3-point delta, all confirmed at source:
1. **Genuinely best-effort (CAI-1250 case A, never blocks the read).** Both actions add, after building the result and before `return`, a `writeAuditLog` wrapped in `try/catch` **and** checking the returned `.error` → `captureActionError` (captured, Sentry-visible), but **never re-thrown, never returned as an error** — control falls through to `return { data, error: null }`. **Empirically proven:** a temp test that makes `writeAuditLog` (a) THROW and (b) return `{error}` — the read still returns the data (`res.error === null`, names resolved) in all 4 cases. This is the *inverse* of #406 RAIL 4 (there the audit WAS the gate and had to block; here the role gate is the access control, so the log must not block) — correct.
2. **Mirrors #416 shape, both actions, org_admin/preparer path only.** `entityType: "unreturned_worklist_view"`, `action: "create"`, `entityId: orgId`, `payload: { viewer_role, class_id, result_count }` — the #416 shape with `class_id` as the drill-down scope (vs #416's page/search). Present in **both** `getKkStudentsByClassAction` and `getKkUnreturnedStudentsByClassAction`, placed **after** the role gate → **no log on the cashier/viewer empty path** (tests assert `writeAuditLog` called for org_admin/preparer with the right payload, `not.toHaveBeenCalled` for cashier/viewer).
3. **No regression.** The delta adds only the two audit blocks + a `user: ctxUser` destructure; the fail-closed gate, the service-role RPC calls, and the aggregation mapping are unchanged context; mig213 (RPC security/grants) byte-unchanged.

## Final verdict — **full PASS.**
Fail-closed security boundary mutation-proven at `09d4318` (positive allowlist, cashier/viewer empty-not-leak, RPCs JWT-unreachable, aggregation byte-identical, both sides); the CAI-1250 audit gap now closed in-PR with a correctly best-effort, #416-shaped log on both name-resolution actions (empirically non-blocking, logged only on the authorised path). Propose-only → console wet-proves + §6.6-applies mig213 both silos on full PASS + CI green + deploy READY. Routing to orch-console (no self-merge).
