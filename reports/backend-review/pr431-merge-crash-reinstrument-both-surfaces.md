# PR #431 — merge-crash re-instrument (shared dashboard layout + duplicates actions → render_diagnostics) — cc-quality review

**VERDICT: PASS** — merge + deploy both silos. Standard single review (diagnostic infra, no migration/PII/money/gate, no cai gate). I remain propose-only; coord owns merge+deploy (end-to-end delegation).
**Reviewer:** cc-quality (opus-4-8) · **Date:** 2026-08-22 · **Head:** 5d3e050f · **Dispatch:** cc-irsyad-coord #31289 (P1, time-sensitive — client waiting on the crash).

## The key risk (shared-layout blast radius) — CLEARED
`dashboard/layout.tsx` runs for EVERY /dashboard/* route/role, so a regression there breaks everything. The change extracts all data-resolution into `resolveDashboardLayoutData()` (try/catch) and leaves `DashboardLayout`'s JSX return UNWRAPPED. Verified the extract is **PURE / behavior-preserving**:
- Same resolution logic in the same order (auth → memberships → super-admin browse-as → org select → view-as → modules/profile/schoolPages); all three redirects (`/login`, `/onboarding`, `/super-admin`) intact INSIDE the try.
- Every DashboardShell prop maps old→new identically: `org?.name`→`d.org?.name`, `activeMembership.org_id`→`d.activeMembership.org_id`, `profile?.display_name ?? user.email ?? "User"`→`d.profileDisplayName ?? d.userEmail ?? "User"`, `user.email ?? null`→`d.userEmail`, effectiveRole/realRole/viewAs.isViewAs/previewRole/typedMemberships/visibleModules/schoolPages/isSuperAdmin/isParentSurface, and SentryUserSync {userId, orgId, role} — all 1:1. No dropped/reordered resolution.
- `tsc --noEmit` clean on the changed files → the `DashboardLayoutData` shape typechecks against consumption (no shape drift).
- **role-smoke 7/7** (coord-run; needs a live server — could-not-run-locally, not could-not-measure) is the live cross-role journey proof; my byte-pure inspection + tsc is the static equivalent.

## Other points — all PASS
2. **Digest-guard:** catch re-throws `NEXT_REDIRECT`/`NEXT_HTTP_ERROR_FALLBACK` UNLOGGED (redirects/notFound never log as crashes → redirect behavior byte-identical); a real throw logs full msg+stack+digest to render_diagnostics (route `"/dashboard/* [server-layout]"`) then RE-THROWs unchanged (the 500 still happens).
3. **Actions** (`merge-candidates.ts` detect/list catch): logRenderDiagnostic ADDED ALONGSIDE the existing captureActionError (pure `+` additions, no removal), awaited, routes `".../duplicates [server-action:detect|list]"`; the swallow / `return INTERNAL_ERROR` is preserved (behavior unchanged).
4. **phase="server" on ALL sites** — the layout+action distinction is carried in the ROUTE string, NOT phase (phase must satisfy render_diagnostics.phase CHECK(server|client) + the TS union — a "server-layout" phase would be a silent no-capture). Correct.
5. **logRenderDiagnostic never-throws** (verified in the #425 audit — wholly try/catch-wrapped, returns ActionResult) and is awaited before re-throw, so a failing sink can never break the layout/actions.

## Gates (at 5d3e050f)
- **lint:all EXIT 0** (check-notfound + hydration-safety 0-new, all 16 gates). **eslint EXIT 0** on layout.tsx + merge-candidates.ts (the react-hooks/error-boundaries rule the split targets is satisfied). **tsc** no errors in changed files. **vitest 10/10** (merge-candidates). **Mutation-proved**: removing the detect-site logRenderDiagnostic reddened the capture test.

**Bottom line: PASS — merge + deploy both silos.** No findings.
