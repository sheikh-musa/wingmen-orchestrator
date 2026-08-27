# Review — PR #422 merge-crash render-diagnostics capture (op#15636)

**Auditor:** cc-quality (standard, no-self-merge) · **Date:** 2026-08-21 · **Verdict: PASS, merge-ready.**
Requested by orch-console (bus #31004, thread `d10f465a`). Diagnostic infra — NO cai gate, no client-data/PII/access change. Time-sensitive.

Pinned HEAD `003e3423cd609ff9cc6ad35a59be6e9666dc0331` (= `gh pr view 422`, MERGEABLE, base `main`). +600/-2, mig216 (**collision-free**). Gates: **lint:all EXIT 0** (incl. check-notfound-boundary) · **11/11**.

## 1. Capture is COMPLETE (both crash origins). CONFIRMED.
- **(a) SERVER:** `requireMergeAdmin()` try/catches `requireRole(["org_admin"])` — the **only server-executable throw source** on this route. The builder's deviation from the "wrap the whole body" spec is **correct**: the two children (`MergeCandidateQueue`, `MergePeopleClient`) are `"use client"`, rendered as opaque client-boundary markers during SSR and never executed server-side, so a try/catch around JSX that only references them catches nothing (ESLint's error-boundaries rule flags exactly this false confidence). On a genuine throw it captures message+stack, logs, and **re-throws unchanged**.
- **(b) CLIENT:** `merge/error.tsx` boundary logs the full client error (`message` + `stack` + `digest`) to `render_diagnostics` (phase `"client"`). So whichever origin the real throw has, it's captured.

## 2. NO false positives. CONFIRMED.
- **Server:** before logging, `requireMergeAdmin` re-throws (unlogged) any error whose `digest` `startsWith("NEXT_REDIRECT")` or `startsWith("NEXT_HTTP_ERROR_FALLBACK")` — and `requireRole` throws exactly those (verified: it uses `redirect("/login")`, `redirect("/dashboard")`, `notFound()`). So normal non-admin redirects + the [Satr] `notFound()` never write a false crash; a genuine error (no such digest) is logged.
- **Client:** `error.tsx` excludes stale-deploy errors (handled + reloaded separately). `notFound()` is kept off the error boundary by `not-found.tsx` (item 4); `redirect()` is framework-handled — neither reaches `error.tsx`.

## 3. logRenderDiagnostic NEVER throws. CONFIRMED.
The whole body is in a `try/catch`; org-resolution is in its **own** nested `try/catch` (→ `orgId = null` on failure); an insert error → `captureActionError` + `return { error }` (not thrown); the outer catch → `captureActionError` + `return { error }`. A diagnostics failure is a returned `ActionResult` error, **never** a thrown exception — it cannot cascade the original crash. Both call sites are fire-and-forget.

## 4. not-found.tsx prevents error.tsx intercepting notFound() (Next 16 streaming). CONFIRMED.
`merge/not-found.tsx` re-renders the ancestor `DashboardNotFound` — so `requireRole`'s `notFound()` renders the intended 404, not error.tsx's "Something unexpected", under Next 16 streaming (the 43e4dfd/qurban regression class). Non-admin UX is **byte-identical** to before (when merge had no error.tsx and `notFound()` bubbled to the ancestor). **`check-notfound-boundary.mjs` passes** in lint:all.

## 5. mig216 render_diagnostics — additive + org_id index. CONFIRMED.
`CREATE TABLE IF NOT EXISTS render_diagnostics` (route, org_id nullable FK, phase, error_message, error_stack, error_digest, created_at); indexes `idx_render_diagnostics_route_created` + **`idx_render_diagnostics_org_id`** (the check-index-coverage-flagged one). RLS enabled: org_admin can read **own-org** rows (`org_id IN (org_members where org_admin)`); NULL-org rows (pre-auth crash) are service-role-only. `BEGIN/COMMIT` (CAI-756-safe), audit genesis. Migration's own PII note: message/stack may incidentally carry ids/route-params but never NRIC/phone/name by construction, read-restricted to org_admin — consistent with "no PII change".

## 6. PURELY ADDITIVE. CONFIRMED.
The only change to the merge page's function is `requireRole(["org_admin"])` → `requireMergeAdmin()` (a wrapper that calls the same `requireRole`, adds a diagnostic side-effect on a non-control-flow throw, and re-throws unchanged). The JSX body is untouched; redirect/notFound/genuine-crash behavior is preserved. No access-model change.

## Verdict
**PASS.** Capture is complete (server requireRole try/catch — correctly the only server throw source — + client error.tsx), false-positive-free (server digest exclusion matching requireRole's redirect/notFound; client via not-found.tsx + framework + stale-deploy skip), logRenderDiagnostic can't throw, not-found.tsx keeps the 404 intact (check-notfound-boundary green), mig216 is additive with the org_id index + org_admin-own-org RLS, and the change is purely additive to the merge page. Propose-only → console wet-proves + applies mig216 both silos + merges. Routing to orch-console (no self-merge).

---

## SUPPLEMENT — mig216 grant/policy hardening (bus #31007, thread `e0dc1e95`)
orch-console's wet-prove surfaced two grant/policy points on `render_diagnostics` that my initial PASS did not flag (I verified the RLS *policy* but not the default table grants — the default-PostgREST-reachability class I should have checked). **Both confirmed at source; I concur.**

1. **Missing explicit `REVOKE anon, authenticated`.** mig216 has **no REVOKE/GRANT statements** → the default Supabase anon/authenticated full table grants (INSERT/UPDATE/DELETE/SELECT/TRUNCATE) remain. RLS contains writes today (no write policy → only service_role writes), but the table holds raw **error stacks** (may carry internal detail), so per the standing revoke-anon/auth reference this is a real defense-in-depth gap: if RLS were ever disabled or a permissive policy added, the stacks become anon/auth-reachable. Fix: explicit `REVOKE ALL ON render_diagnostics FROM anon, authenticated` (service_role keeps its grant for the insert). 2 lines.
2. **The org_admin read policy is UNNECESSARY — drop it.** Verified: **no app surface reads `render_diagnostics`** — the only reference in `src/` is the single `svc.from("render_diagnostics").insert(...)` (a service-role write); there is no `.select(...)` anywhere. The only intended reader is console via direct/service-role DB. So the "org_admin can read own-org diagnostics" SELECT policy grants nothing the app needs and exposes raw error stacks to org_admins. Fix: DROP the policy → RLS-on / no-policy = app-invisible, console reads via service_role/direct. Strictly better than REVOKE alone (org_admins also can't read stacks).

**Recommendation: fix BOTH in-PR** (cheap 2-line changes, quick re-wet-prove; the table is new so getting grants right at creation is the bar, and it holds error internals). Neither blocks the capture's *purpose* — the 6-point capture verdict above stands unchanged — so if client urgency forces it, the `REVOKE` is the floor and the policy-drop a same-day follow. Preferred end state: `render_diagnostics` is a **service_role-only sink** (service-role insert, console-only read), no anon/auth surface, no org_admin stack exposure.

**Revised verdict: PASS on the diagnostic capture (6 points); mig216 grant/policy should be hardened in-PR (REVOKE anon/auth + DROP the org_admin read policy → service_role-only) before apply.**
