# shipforge PR #26 — N1/N2 fast-follows — ✅ CLEARED after re-verify (ccb9d8d)

> **RE-VERIFY UPDATE (ccb9d8d, 2026-08-17): CLEARED — merge-ready.** cc-shipforge re-gated on `VERCEL_ENV === "production"` (not `NODE_ENV`) and added a RED-first preview-case test. I re-verified empirically to my own bar:
> - **Scenario matrix 5/5 PASS:** preview (`VERCEL_ENV=preview` + `NODE_ENV=production` + no `SITE_PUBLIC_URL`) → returns req.url origin, **NO throw** (the bar); production + no config → **throws**; production + `SITE_PUBLIC_URL` → returns it; local dev → fallback no throw; preview + `SITE_PUBLIC_URL` → returns it.
> - **Preview-case test is a genuine RED-first test (not taken on the lane's word):** reverting the guard to `NODE_ENV === "production"` makes the preview-case test go **✖ RED** ("does NOT throw on a Vercel PREVIEW deploy…"); restored → green.
> - 19/19 affected tests pass on head. Full 282/290 + tsc not run by me (no-install) — CI is the gate.
> - **Rule codified (orch-console #23645):** the gate must match the SCOPE of the variable it guards — `NODE_ENV` is a build-mode flag, not an environment discriminator; a production-only env var must be guarded by `VERCEL_ENV`. The PR#24 SESSION_SECRET contrast (safe on `NODE_ENV` only because that secret is set on all envs) is the sharp version.
>
> The original CHANGES-REQUESTED review that found the bug is preserved below for the record.

---

# (original) shipforge PR #26 — N1/N2 fast-follows — ⚠️ CHANGES REQUESTED (1 blocker: N1 breaks preview; N2 verified good)

**Reviewer:** cc-quality (Opus 4.8) · **Date:** 2026-08-17
**PR:** sheikh-musa/shipforge#26 `fix/gate2-t1-fastfollow-n1n2` (commit bf4919b) · 3 files, +45/−14
**Requester:** orch-console (#23633) — "review as an author would be reviewed" (these are MY PR#25 findings implemented).
**Not a production/deploy risk:** prod has `SITE_PUBLIC_URL` set (Gate 1b), so the hardened fallback is unreachable in prod. The N1 defect is a **preview-only regression.**

## Verdict: fix N1 before merge. N2 is correct.

## N1 · BLOCKER (preview regression) — the guard fires on Vercel Preview, where it must not
`requestBase` (app/lib/requestBase.ts) gates the throw on `process.env.NODE_ENV === "production"`:
```
const configured = explicit || process.env.SITE_PUBLIC_URL || process.env.SITE_BASE_URL;
if (configured) return configured.replace(/\/$/, "");
if (process.env.NODE_ENV === "production") { throw new Error("SITE_PUBLIC_URL (or SITE_BASE_URL) is required in production"); }
return new URL(req.url).origin.replace(/\/$/, "");
```
**Vercel runs PREVIEW deployments with `NODE_ENV=production` too** — `NODE_ENV` is a build-mode flag (production for every non-local build), NOT an environment discriminator. The documented discriminator is **`VERCEL_ENV`** ∈ {`production`,`preview`,`development`} (Vercel System Environment Variables docs, "Available at: Both build and runtime"). `VERCEL_ENV` appears **nowhere** in the repo.
- orch-console deliberately set `SITE_PUBLIC_URL` on **Production only** (Preview left unpinned so per-deployment preview URLs keep working). So on a **preview** deploy: `configured` is undefined **and** `NODE_ENV==="production"` → **`requestBase` throws on every onboarding-link build → preview onboarding breaks.**
- **Empirically demonstrated:** with `NODE_ENV=production`, `SITE_PUBLIC_URL`/`SITE_BASE_URL` unset (the preview state), `requestBase(req)` → `THREW: "SITE_PUBLIC_URL (or SITE_BASE_URL) is required in production"`.
- **Fix:** gate on `process.env.VERCEL_ENV === "production"` (not `NODE_ENV`). Then preview (`VERCEL_ENV="preview"`) keeps the `req.url` fallback and only true production throws.
- **Test gap that let it through:** the N1 tests cover `NODE_ENV=production→throw` and `development→fallback`, but NOT the preview case (`VERCEL_ENV=preview` / `NODE_ENV=production` with no `SITE_PUBLIC_URL` → must NOT throw). Add it.
- **I own the ambiguity in my own recommendation:** my N1 note said "throw in production," and the correct discriminator for a **production-only** env var is `VERCEL_ENV`, not `NODE_ENV`. Contrast PR#24's SESSION_SECRET guard, which safely uses `NODE_ENV==="production"` — that's fine ONLY because SESSION_SECRET is set on prod+preview+dev, so it never spuriously fires. The idiom is safe there and unsafe here; the gate must match the env-var's scope.

## N2 · VERIFIED GOOD (empirically, not taken)
The reshaped F5 "does NOT follow a redirect to a blocked (metadata) host" test now uses the literal-IP form (`93.184.216.34` → 302 → `169.254.169.254`) with a redirect-mode-aware mock. **RED-check re-run by me:** reverted `guardedFetch` to the old `redirect:"follow"` single-call form and ran `tests/content-view.test.ts` → the metadata test **✖ FAILS** (goes RED for the right reason), then restored → GREEN. cc-shipforge's "goes RED on reverted code" claim holds. **18/18** on the two affected files at PR head.

## Coverage / caveats
- Ran the 2 affected test files (18/18). **COULD NOT MEASURE** the full 281/289 + tsc — no-install checkout (same honest caveat as #24/#25); CI is the gate for the full suite.
- N1 is not a production/deploy risk (prod `SITE_PUBLIC_URL` set); it is a preview-onboarding regression, and it defeats part of the point of a "correct fail-closed control." Merge after the `VERCEL_ENV` change.

— cc-quality
