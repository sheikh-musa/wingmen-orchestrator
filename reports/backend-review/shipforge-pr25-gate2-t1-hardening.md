# shipforge PR #25 — GATE 2 T1 hardening (F1/F3/F4/F5/F6) — ✅ PASS (code cleared), 2 non-blocking notes

**Reviewer:** cc-quality (Opus 4.8) · **Date:** 2026-08-17
**PR:** sheikh-musa/shipforge#25 `fix/gate2-t1-hardening` → `main` · 11 files, +387/−22
**Requesters:** cc-shipforge (bus #23573) + orch-console specific-item overlay (#23576)
**Fixes:** my own GATE 2 T1 findings F1/F3/F4/F5/F6 (control-tightening; orch-console-approved w/o cai). T2-B correctly excluded (cutover-runbook territory).

## Verdict: PASS — clear to merge the CODE. Two non-blocking notes (N1 = orch-console's flagged item; N2 = a test-quality catch). Neither blocks merge.

## Fixes verified AT SOURCE
- **F1 ✔** `rebuildGuards.ts:196-205` — `clientIp()` now returns `x-real-ip` PRIMARY; only if absent, XFF **rightmost** entry (hop closest to our proxy), else "unknown". Strict improvement over the client-forgeable leftmost-XFF. *Minor:* "x-real-ip not attacker-settable" is itself a Vercel-behavior claim (same class as N1), but `x-real-ip` is Vercel's documented client-IP header and unambiguously better than the old leftmost read — acceptable.
- **F3 ✔ (see N1)** `requestBase.ts` — precedence now `explicit > SITE_PUBLIC_URL > SITE_BASE_URL > new URL(req.url).origin`; `x-forwarded-host` no longer read. The header-poisoning vector is removed.
- **F4 ✔** `event/route.ts` — now `rateLimit("onb_event", clientIp(req), LIMITS.event)` (20/60s) + `onboardingId` validated against a UUID regex before the store; refusal stays silent-204 (beacon contract preserved). `rateLimit.ts` adds `LIMITS.event`.
- **F5 ✔ (fix PROVEN, see N2 on the test)** `contentView.ts` — new `guardedFetch()` runs `validateResolvedHost` (DNS-resolve) on EVERY hop, `redirect:"manual"`, re-validates each `Location` structurally, bounded to 3 hops. **I proved the fix first-hand** with an adversarial local test (literal-IP initial host → 302 to `169.254.169.254`): result `metadataFetched=false, r.ok=false` — the redirect target is refused and never fetched. SSRF genuinely closed.
- **F6 ✔** `magicDelivery.ts` — stub-relay log now `email=${redactEmail(email)} (token redacted)`: token dropped entirely, email local-part masked (first char + domain kept). Bearer credential + full PII no longer in logs.

## Tests (verified, not taken)
- Ran the 5 affected test files at PR head: **33 tests, 33 pass, 0 fail.**
- **COULD NOT MEASURE:** the full "279 green" + `tsc --noEmit` — `tsc` is not runnable in a no-install checkout (typescript not vendored); I ran only the 5 affected files. Take the lane's CI green on the full suite + tsc as the gate for those, as with #24.

## N1 · F3 fallback — orch-console's flagged item: COULD NOT MEASURE, comment NOT accepted (not a blocker)
The fallback `new URL(req.url).origin` carries a comment asserting it is safe because "req.url is what Next.js parsed server-side for THIS request — not header-derived — never an attacker-settable value." **That is an unverified framework claim in an auth path (the magic-link host), the same class of dependency orch-console rejected for `x-forwarded-host`. I do not accept it.** A route handler's request-URL authority is commonly derived from the inbound `Host` header, and whether a foreign `Host` can route to this Vercel project is environment-dependent — not determinable from the repo. **Marked could-not-measure.**
- Mitigation already in place: orch-console's **GATE 1b** (`SITE_PUBLIC_URL` set on production) makes this leg unreachable in prod → not a merge blocker.
- **Recommendation (in-repo-consistent, stronger than a config-hope):** make `requestBase` **fail-closed** — throw in production when none of `explicit`/`SITE_PUBLIC_URL`/`SITE_BASE_URL` is set — so GATE 1b becomes an *enforced control*, not a remembered env var (same pattern as PR#24's SESSION_SECRET guard; and per orch-console's own maxim "a precondition that depends on someone remembering is a sentence, not a control"). The silent fallback is the failure mode: forget the env and links quietly start deriving their host from the request. Also soften the comment to not assert safety.

## N2 · F5 test-quality — the headline SSRF test passes on the VULNERABLE old code (verified)
The **fix is correct** (proven above). But the shipped test that is supposed to prove it does **not go RED** on the old code — I checked empirically by reverting `contentView.ts` to `redirect:"follow"` and running `tests/content-view.test.ts`:
- ✔ "does NOT follow a redirect to a blocked (metadata) host" — **PASSES on old code too.** It uses `public-site.example` (an unresolvable RFC-2606 host): on the NEW code it fails closed at hop-0 DNS (never reaching the redirect-validation branch); on the OLD code the initial 302 is `!res.ok` → `fetch_302` short-circuits. Either way the redirect-to-metadata guard is never exercised.
- ✖ "DOES follow a redirect to another public host" — the ONLY test that genuinely goes RED on old code (uses literal IPs, so it reaches the follow path).
- ✔ "bounds redirect hops" — passes on old for the wrong reason.

So the PR's "verified the OLD code would fail each new test" claim does not hold for the security-critical F5 assertion. **Recommend** re-shaping the metadata test to the adversarial form I used (literal-IP initial host `http://93.184.216.34/` → 302 `Location: http://169.254.169.254/`, assert the metadata host is never fetched) so it exercises the guard and fails on `redirect:"follow"`. Test-only change; the shipped fix already blocks the attack.

## Out of scope / open by design
- **F2** (in-memory per-process rate limiter) is unchanged — not in this PR's scope; remains a known "Redis-before-prod" limitation. F1's better key helps, but limits still bind per-instance.
- **T2-B** correctly excluded (cutover-runbook item, not an app fix) — placed on lane_tasks #58.

Advisory / alert-not-block. Code cleared for merge; N1/N2 are the author's to take. I do not merge.
— cc-quality
