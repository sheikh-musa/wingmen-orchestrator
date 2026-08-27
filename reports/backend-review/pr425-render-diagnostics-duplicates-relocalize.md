# PR #425 — relocalize merge-crash capture to /dashboard/people/duplicates — cc-quality review

**Verdict: PASS.** App-code only, no migration. Faithful mirror of the shipped #422 merge pattern. Cleared for cc-irsyad-coord to merge + deploy-verify.
**Date:** 2026-08-22 · **Reviewer:** cc-quality (Sonnet 5, op#14199) · head 44926e4 (off main incl #422)
**PR:** https://github.com/sheikh-musa/ihsanos/pull/425 (`feat/render-diagnostics-duplicates-relocalize`)
**Dispatch:** cc-irsyad-coord #31058 (P1, coord driving end-to-end per console delegation #31057; no cai gate — non-PII/money/mig surface; report PASS/FAIL to coord).

## Scope verified empirically
4 files vs origin/main, **all app-code, no migration** (checked out at head, diff-confirmed): `duplicates/page.tsx` (+36/-1), new `duplicates/error.tsx`, `duplicates/not-found.tsx`, `duplicates/__tests__/error.test.tsx`. render_diagnostics table already live both silos (mig216 + mig217 hardening — my prior confirm).

## Checks
1. **Server capture (`requireDuplicatesAccess`)** — structural mirror of merge's `requireMergeAdmin`: re-throws `NEXT_REDIRECT`/`NEXT_HTTP_ERROR_FALLBACK` **unlogged** (Next control-flow signals), logs a genuine `requireRole` throw to render_diagnostics (phase=server) pre-redaction, then re-throws unchanged. **Denial-safe:** the [Satr] gate's `notFound()` throws `NEXT_HTTP_ERROR_FALLBACK` → re-thrown unlogged, so denied non-preparer/non-admin users generate NO diagnostic rows; only genuine server crashes are captured.
2. **Role list unchanged** — `requireRole(["org_admin","preparer"])`, the route's pre-existing gate (diff old line identical). They correctly did NOT copy merge's narrower `["org_admin"]`. Coord's claim confirmed.
3. **Logger cannot mask the crash** — `logRenderDiagnostic` is throw-proof by construction (entire body `try/catch`, inner org-resolution its own `try/catch`, JSDoc "MUST NEVER THROW ITSELF"; returns `ActionResult` with `error` set on failure). So the `await logRenderDiagnostic()` in the catch cannot alter/mask the re-thrown error. Best-effort diagnostics — fail-open is correct-by-design HERE (NOT a fail-closed gate; contrast `fail-closed-verify-callee-contract` where the discarded `.error` was a real audit bug). No mutation-prove owed.
4. **Client boundary (`error.tsx`)** — mirrors merge/error.tsx: stale-deploy → reload (unlogged); genuine crash → `logRenderDiagnostic` (phase=client). **`not-found.tsx`** — Rule B sibling for the new error boundary (delegates byte-identically to ancestor `DashboardNotFound`; module-boundary pragma).
5. **lint:all at pinned HEAD (44926e4) — EXIT 0, all 16 gates green** (incl lint:notfound Rule B, lint:test-presence, lint:module-boundaries pragma, lint:pii-read-role, lint:hydration-safety).
6. **vitest — 8/8 pass** (duplicates error.test.tsx 4 + merge error.test.tsx 4). Duplicates test imports the shipped `../error` and asserts the exact `logRenderDiagnostic` call-shape + the stale-deploy branch (no log, reload instead) — exercises the real path.

## Non-blocking note (parity, not a #425 defect)
The server-capture branch (`requireDuplicatesAccess`/`requireMergeAdmin` catch: NEXT_* re-throw vs log-genuine) is **not unit-tested on either route** — merge (the shipped #422 baseline) has the same gap. Optional follow-up to add a server-path test for both; does not block #425, which matches the established pattern exactly.

**Bottom line: PASS — merge + deploy-verify.**
