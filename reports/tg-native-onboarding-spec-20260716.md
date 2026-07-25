# TG-native merchant onboarding — design spec (op#4529)

**Status:** DESIGN-FIRST, for Nazim → operator LOOK before any build. Author: cc-orchestrator (hub). Date: 2026-07-16. Ref: op#4529, Nazim #8936/#8985. Grounded in a read-only code map of `~/wingmen/projects/ihsanos`.

## Headline: this is ~80% already built — it's an ADD, not a from-scratch build

A working Telegram-native merchant self-onboarding bridge already exists (**CAI-RESP-294**, migration 075). It already: HMAC-validates initData server-side, creates a Supabase auth user with **no email/password**, provisions a self-owned org, and mints a session — entirely in-Telegram. **The one gap vs. op#4529:** it does not let the merchant enter a **business name + chosen handle** — it auto-generates both (`shop-<uuid8>` slug, Telegram display name as the org name). So op#4529 = "add a name+handle capture step to the existing bridge + finish two rough edges." Small and well-scoped.

One correction to the op#4529 framing (#8936 said "org created via existing `createOrganizationAction`"): the TG path does **not** use `createOrganizationAction` — it uses a thinner SECURITY DEFINER RPC (`provision_tg_merchant_org`). Reconciling those two paths is part of this spec (§3).

## What already exists (reuse, don't rebuild)

| Piece | Location | State |
|---|---|---|
| initData HMAC verify (constant-time, auth_date freshness) | `src/shared/lib/telegram-initdata.ts:14` | ✅ built + tested |
| Mini App entry `/m` (reads WebApp initData, POSTs to bridge) | `src/app/m/page.tsx` | ✅ but auto-provisions, no form |
| Merchant-session route (maxAge 60s, replay guard, IP rate-limit) | `src/app/api/tg/merchant-session/route.ts:23` | ✅ built |
| Provision orchestration (HMAC→replay→map→provision→audit) | `src/modules/storefront/tg-bridge/session.ts:51` | ✅ built |
| TG→Supabase auth user (deterministic internal email, no pw) | `src/modules/storefront/tg-bridge/provision-org.ts:56` | ✅ built |
| Session mint (magiclink→verifyOtp→cookies) | `src/modules/storefront/tg-bridge/session-mint.ts:24` | ✅ built |
| Org-provision RPC (atomic org+member+tg mapping, idempotent) | `supabase/migrations/075_tg_identity_bridge.sql:98` | ✅ but hard-codes name/slug |
| Slug uniqueness (DB UNIQUE + live check) | `007_org_slug.sql:2`, `checkSlugAvailabilityAction` `onboarding.ts:58` | ✅ built |
| Rich org setup (tags, donation_categories, clients, modules, perms, audit) | `createOrganizationAction` `src/actions/onboarding.ts:105` | ✅ but web-only (needs authed user) |

## The design — three changes

### 1. Capture step (the actual new surface)
In `/m`, after initData identity is established and IF the merchant has no org yet, show a one-screen form: **Business name** + **Handle** (with live availability). No email, no password.
- **Handle field:** debounced live check via the existing `checkSlugAvailabilityAction` (`onboarding.ts:58`); enforce the existing slug regex `^[a-z0-9][a-z0-9-]*[a-z0-9]$` (2–63). Show taken/available inline. Suggest a default derived from the business name (slugified) that they can edit.
- **Name field:** reuse `createOrgSchema`'s name rule (1–200). 
- **Org tags:** op#4529 keeps it frictionless — default to a sensible single tag (e.g. the business type) rather than a tag picker; can be edited later in-app. (Decision point for operator — §Open Qs.)
- On submit → POST name+handle to the bridge (below). Existing-org and returning users skip the form (idempotent).

### 2. Thread name+handle through the two provision seams
Today the slug is hard-coded and the RPC ignores a caller-supplied slug:
- `session-deps.ts:77` hard-codes `slug: shop-${uuid8}` → change to pass the merchant's handle.
- `provision-org.ts` / the `provision_tg_merchant_org` RPC (`075...sql:98`) → accept `p_org_name` + `p_slug` and use them instead of auto-values.
- **Slug-collision safety:** the RPC currently only catches the `telegram_users` PK unique-violation, NOT a `organizations.slug` collision — a slug clash would bubble as a 500. Add explicit slug-collision handling: server re-checks availability at submit AND the RPC returns a typed "handle taken" result the form can show (don't rely on the race-y client check alone).

### 3. Unify the two org-creation paths (so TG merchants aren't second-class)
The TG RPC is much thinner than `createOrganizationAction`: it skips `org_tags`, `donation_categories` seeding, the `clients` ecosystem link, module config, permission seeding, and the audit-log write. A merchant who onboards via Telegram should get the **same complete org** as one who onboards on the web.
- **Recommended:** extract the rich provisioning body of `createOrganizationAction` into a shared `provisionOrgCore({ userId, name, slug, tags })` (service-client, RLS-bypass — it already runs that way for new tenants) callable by BOTH the authed web action AND the service-role TG bridge. The TG bridge calls it after minting the auth user. This kills the divergence permanently (one code path, two entry points) rather than duplicating the rich setup into the RPC.
- Lower-effort alternative: enrich the RPC to seed the same rows. Rejected — it re-duplicates logic and drifts (the exact CC-SUBSTRATE class we keep paying for).

## Security (must not weaken the existing bridge)
- initData HMAC stays server-side (`verifyTelegramInitData`, maxAge 60s), replay guard (`hash`+`auth_date`), pre-auth IP rate-limit — all unchanged. Name/handle are collected AFTER initData validation; provision still keys off the validated `telegram_user_id`, never client-asserted identity.
- Handle uniqueness enforced at the DB (`slug UNIQUE`) — the form check is UX only; the DB + collision-safe RPC are the real guard.
- One-org-per-user + idempotency: existing guards (`telegram_users` unique, one-org check) carry over.
- Reserved handles: add a small denylist (e.g. `admin`, `super-admin`, `api`, `m`, `shop`) so a merchant can't grab a routing-sensitive slug.

## Rollout (design-first; build only after operator LOOK)
1. Extract `provisionOrgCore` (refactor, no behavior change on the web path — verify web onboarding still green).
2. Wire the TG bridge to pass name+handle + call `provisionOrgCore`; add slug-collision result.
3. Build the `/m` capture form (live handle check).
4. Drive it end-to-end in the real Mini App (initData → form → org created → session) before it touches a real merchant. Gated: cc-reviewer money/identity review (auth-user creation + org provisioning are sensitive), no residency change (pooled ceayj merchants).

## Open questions for the operator (the LOOK)
1. **Org tags at signup:** default a single sensible tag (frictionless, recommended), or show a small picker?
2. **Recovery email:** the bridge already flags `requires_email_capture` — prompt for a real recovery email at signup, later, or never (Telegram identity is the account)?
3. **Reserved-handle policy:** OK to block a small set of routing-sensitive handles?
4. **Scope:** this is the storefront/merchant onboarding (Hadi/Hadramawt class). Confirm it's merchants only for v1 (not the tabung/irsyad org type).

---
*No code is written until the operator's LOOK. On approval this is a small, well-scoped build (1 form + 2 seams + 1 refactor), driven end-to-end before any real merchant sees it.*
