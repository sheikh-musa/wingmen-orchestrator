# TG-native merchant onboarding — BUILD brief (op#4529)

**From:** cc-orchestrator (hub) · **Authorized:** operator GREEN-LIGHT via Nazim #9369 (op#4529) · **Date:** 2026-07-17
**Design spec (read in full FIRST):** `reports/tg-native-onboarding-spec-20260716.md`
**Your worktree:** `~/wingmen/ihsanos-wt/tg-onboarding` (branch `feat/tg-native-onboarding`, off origin/main f623979). Isolated — WP-C runs concurrently in a different worktree; stay in yours.

## What this is
~80% already built (CAI-RESP-294 / migration 075 TG bridge). op#4529 = ADD a business-name + handle capture step to the existing in-Telegram merchant bridge + finish two rough edges + UNIFY the two org-creation paths. Small, well-scoped: **1 form + 2 seams + 1 refactor.** AUTHORED build — do NOT merge, do NOT push to main; hub reviews+gates.

## The 4 operator decisions (now settled — build to these)
1. **Org tag at signup:** ONE sensible default for v1 (frictionless), NO picker. Tag editable later in-app.
2. **Recovery email:** OPTIONAL, prompted AFTER onboarding completes, NEVER blocks signup. (The bridge already flags `requires_email_capture` — wire the post-onboarding prompt; don't gate signup on it.)
3. **Reserved-handle denylist:** YES, standard set (e.g. `admin`, `super-admin`, `api`, `m`, `shop`, `support`, `www`, `app`). Enforce server-side.
4. **Scope:** v1 MERCHANTS ONLY. Not the tabung/irsyad org type. (Customers already shop via the Mini App.)

## Build (per spec §The design — three changes)
1. **`provisionOrgCore` refactor FIRST (no behavior change):** extract the rich provisioning body of `createOrganizationAction` (`src/actions/onboarding.ts:105`) into a shared `provisionOrgCore({ userId, name, slug, tags })` (service-client, RLS-bypass) callable by BOTH the authed web action AND the service-role TG bridge. Verify the WEB onboarding path stays green after the extract (regression guard) BEFORE wiring TG.
2. **Thread name+handle through the 2 provision seams:** `session-deps.ts:77` (stop hard-coding `shop-<uuid8>` — pass the merchant handle); `provision-org.ts` + the `provision_tg_merchant_org` RPC (`075...sql:98`) accept `p_org_name`+`p_slug`. **Slug-collision safety:** server re-checks availability at submit AND the RPC returns a typed "handle taken" result (don't rely on the racy client check). The TG bridge calls `provisionOrgCore` after minting the auth user → TG merchants get the SAME full org as web (tags, donation_categories, clients link, modules, perms, audit) — not second-class.
3. **`/m` capture form (the new surface):** after initData identity is established AND if the merchant has no org, show a one-screen form: **Business name** (reuse `createOrgSchema` 1–200) + **Handle** (debounced live check via `checkSlugAvailabilityAction` `onboarding.ts:58`; slug regex `^[a-z0-9][a-z0-9-]*[a-z0-9]$`, 2–63; suggest a slugified default from the name, editable). No email, no password. Existing-org/returning users skip the form (idempotent).

## Security (must NOT weaken the existing bridge)
initData HMAC verify stays server-side (maxAge 60s), replay guard, pre-auth IP rate-limit — UNCHANGED. Name/handle collected AFTER initData validation; provision keys off the validated `telegram_user_id`, never client-asserted identity. Handle uniqueness enforced at the DB (`slug UNIQUE`); the form check is UX only. Reserved-handle denylist server-side.

## Proof / test (HARD REQUIREMENTS)
- Automated: unit + an e2e that drives `/m` with a VALID simulated initData (HMAC-signed test payload) → form → org created → session minted → lands on the live store. Assert the org is the FULL org (tags/donation_categories/modules/perms/audit rows present), slug = chosen handle, collision path returns typed "handle taken". Web onboarding regression green. tsc 0, lint 0, `next build` green. Paste REAL output.
- **REAL Mini App e2e (operator/hub-gated):** the spec's "signup → org → live store all inside real Telegram" needs a real TG client — you CANNOT fully self-drive that. Build + prove with simulated-initData e2e, then REPORT ready-for-real-TG-drive; the hub coordinates the real-Mini-App verification (likely a test TG account / operator) BEFORE any real merchant sees it. Flag exactly what a human must click to verify.

## Report back to cc-orchestrator (agent_messages)
Branch + SHA; files changed; the `provisionOrgCore` diff + confirmation web path unaffected; the RPC/migration change (if any new migration — money-adjacent, register known-unapplied, do NOT apply); simulated-initData e2e output; what remains for the real-TG drive. Storefront = money-adjacent → hub runs cc-reviewer (identity/auth + org-provisioning) + eyeball before merge. Trust-nothing: hub re-runs your tests.
