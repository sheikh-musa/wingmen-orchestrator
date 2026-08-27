# shipforge PR #24 — SESSION_SECRET prod-required guard — ✅ PASS (merge-ready, code cleared)

**Reviewer:** cc-quality (Head of Quality, Opus 4.8) · **Date:** 2026-08-16
**PR:** sheikh-musa/shipforge#24 `fix/session-secret-prod-guard` → `main`
**Head SHA reviewed:** `8dbe91168aeba64c1aa45f815fa28c4cb3f2c63c`
**Requester:** cc-shipforge (bus #23428) · orch-console-approved dormant-verification fix
**Scope:** 6 files, +85/−15 (3 src guards + 3 tests). Control-tightening only.

## Verdict: PASS — clear to merge the CODE.
Genuine fail-closed security fix. No blockers. Two non-blocking nits + one description
correction below.

## What it does
`getSecret()` in `app/lib/{session,handoff,magiclink}.ts` previously fell back to the
public literal `"dev-session-secret-change-in-prod"` when both `SESSION_SECRET` and
`REVALIDATE_SECRET` were unset — a forgeable session cookie / magic-link / cross-domain
handoff baton in prod (known default = anyone reading the repo can forge for any accountId).
Now: return the configured secret if present; else in `NODE_ENV==="production"` **throw**;
else (dev/test) keep the dev default. Dev/test behavior unchanged. Arms nothing, sets no secret.

## Verified AT SOURCE (verify-not-assert)
1. **Security logic correct / fail-closed.** Guard fires only in the already-broken
   misconfigured-prod state (secret unset). Healthy prod (secret set) unchanged; dev/test
   unchanged. Empty-string `SESSION_SECRET=""` also correctly falls through to the prod
   throw (`??` keeps `""`, `if (secret)` rejects it). Verify* now throws instead of
   returning null in that misconfigured state — but that state is fully broken anyway, so
   fail-closed, not a regression.
2. **Consistency claim TRUE.** The "mirrors HANDOFF_VERIFY_BEARER" claim checks out:
   `app/api/onboarding/handoff-verify/route.ts:54` has the real existing
   `NODE_ENV==="production" && !bearer → refuse` prod-required pattern. The new guard is the
   same posture.
3. **No import/build-time risk.** `getSecret` is only called inside `hmacKey` (request-time),
   never at module top-level — the prod throw cannot break `next build` (build has the env set).
4. **Tests authentically exercise the SHIPPED guard (test-coverage gate).** They call the real
   `signHandoff`/`signMagic`/`signSession` → real `getSecret` (reads `process.env` at call time,
   so setting `NODE_ENV=production` before the call works). **RED-check performed:** reverting
   the session.ts guard makes `getSecret production guard` FAIL (✖) → the test is real, not vacuous.
5. **Affected suite green.** `tests/{session,handoff,magiclink}.test.ts` → **27/27 pass, 0 fail**,
   including the 3 new prod-guard tests, run in node-test's per-file process isolation.

## Test-suite reconciliation (I ran it — did NOT trust "42 green")
- Real full suite is **235 tests / 65 suites**, not 42 (PR body says "42-test repo suite"). The
  "42" is a stale/subset count — not material to correctness, but the description is inaccurate.
- My no-install run showed **7 file-level failures** — ALL `ERR_MODULE_NOT_FOUND: Cannot find
  package 'postgres'` (invite-scope, onboarding-e2e, onboarding-telegram, ops-alert,
  prod-onboarding, residency-shipforge-app, unlock-audit). Cause = **no `node_modules` in my
  fresh checkout**, not code. These are DB/integration files this PR does not touch; they pass
  under `npm install`/CI. **Not a regression, orthogonal to this PR.**
- **Caveat (honest):** I did NOT `npm install` to re-run those 7 DB-dependent files green. The
  PR changes no DB path, so this doesn't affect the merge decision — but the author/CI green on
  the full 235 should be the gate for those.

## Non-blocking nits (author's discretion — do not block merge)
- **N1 (cosmetic):** `tests/handoff.test.ts` new test deletes `REVALIDATE_SECRET` but only
  restores `NODE_ENV` in `finally` (relies on `afterEach` for `SESSION_SECRET`, never restores
  `REVALIDATE_SECRET`). Harmless — it's the last test in the describe and node-test isolates
  files in separate processes — but inconsistent with the magiclink/session siblings which
  save/restore all three. Tidy for symmetry.
- **N2 (info):** the guard THROWS (→ 500 at the API boundary) whereas the mirrored
  HANDOFF_VERIFY_BEARER pattern returns a graceful 503 misconfig. For a signing primitive a
  loud throw/fail-closed is correct/arguably better; noting only so the choice is deliberate.

## Footing
Advisory review, alert-not-block. I cleared the CODE for merge; I do not merge or set secrets.
Operational precondition (author/deployer owns): `SESSION_SECRET` MUST be set in every prod
environment before/at this deploy — after merge, an unset secret changes a silent-insecure prod
into a hard-fail prod (that is the intended tradeoff, but it must be provisioned).

— cc-quality
