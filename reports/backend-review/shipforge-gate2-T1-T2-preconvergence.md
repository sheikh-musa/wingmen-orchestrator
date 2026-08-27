# shipforge GATE 2 — Tracks T1 (runtime) + T2 (migrations) — pre-convergence review

**Reviewer:** cc-quality (Head of Quality, Opus 4.8) · **Date:** 2026-08-17
**Scope owner:** lane_tasks #58 (GATE 2 SCOPE, orch-console 2026-08-16 16:40Z) · pointer bus #23547
**Repo:** `~/wingmen/projects/shipforge-deployed` · **Range:** `6de1390..origin/main` (HEAD `fa5c9c5`)
**My tracks:** T1 runtime (app/ + bot/ + concierge/ + pipeline/ + proxy.ts + package.json, 63 files / 5,796 ins) · T2 db/migrations (17 files / 1,037 ins). T3 (tests) + T4 (pilot content) are cc-shipforge's.
**Method:** app/ read by 4 parallel review subagents; **every MED+ finding independently re-verified by me at source**; T2 read in full by me; bot/pipeline/concierge skimmed for egregious issues. GATE 0 not given — reviewing merged code, **no deploy implied**.

---

## HEADLINE

1. **No db/ migration rides the deploy.** `vercel --prod` = `next build` only (package.json scripts); no `vercel.json`, no postinstall/migrate hook, no `.sql` execution in the build path. T2 is **deploy-neutral** — every SQL file is an out-of-band apply artifact.
2. **The per-account DB-RLS path is UNWIRED (CORRECTED 2026-08-17 — reconciliation of orch-console #23585).** My first draft said this layer is "dormant because `ready()` is false" — that was evaluated against the var-absent local/test state and is **WRONG for production**: `SHIPFORGE_DB_URL` has been set on Production for 26d (orch-console measured, name+target only), so `shipforgeDb.ready()` is **TRUE in prod** (same error-shape as the SESSION_SECRET story — code said one thing, deployed env another). The conclusion nonetheless holds on **firmer ground**: verified across all of `app/`, the account-scoped RLS path `withAccount()` has **ZERO callers**, and the five tenant-data stores (accounts/sites/site_links/claim_tokens/change_requests) never import `shipforgeDb` — they use the Supabase REST **service_role** path **unconditionally** (not gated on `ready()`). The only pg-path consumers are `unlockAudit.ts` + `opsAlert.ts`, both via `sys()` (system path, no account GUC), both gated on `ready()` → **live in prod** for two non-PII wingmen-own operational tables (`unlock_audit`, `ops_alerts`, permissive RLS). So tenant isolation in production rests on **app-layer route checks, not DB RLS — because the RLS path is unwired, not because a flag is off.** "Just set `SHIPFORGE_DB_URL`" does NOT move tenant data onto RLS. Whether the two audit tables' pg writes actually reach an enforcing `ceayj.shipforge` is a **CAI-978 OBSERVE item** (the DSN target and whether the schema/role/RLS was ever provisioned are not code-inferable; a failed pg insert is swallowed fail-safe → silent no-op).
3. **Top money-risk is FAIL-CLOSED (good).** Entitlement gate and deploy-signal auth both deny-by-default / fail-loud (verified). No SQL/PostgREST injection. No XSS sink. No client secret exposure observed.
4. **db/ceayj-tenant/* are LIVE-silo (ihsanos `ceayjeamtmcyzzvqflus`) migrations** — §6.6 applies. **Already correctly routed to cai/Nazim** in-file (CAI-430/431/432); NOT to be applied on this convergence.

Verdict on my tracks: **no deploy-BLOCKER found in T1/T2 for a Gate-0-authorised `vercel --prod`** (given the Gate-1 SESSION_SECRET precondition already tracked on #58). A set of real hardening findings + one migration-drift finding to fix before the ceayj cutover, below.

---

## T1 — RUNTIME FINDINGS (ranked; ✔ = I verified at source first-hand)

**F1 · HIGH ✔ — Rate-limit key is attacker-forgeable (leftmost X-Forwarded-For).** `app/lib/rebuildGuards.ts:192` `clientIp()` returns `xff.split(",")[0].trim()` — the LEFTMOST XFF entry, which the client controls (Vercel appends the real IP to the right). `x-real-ip` is present but used only as a fallback. Every onboarding throttle keyed on it (`onb_start` email-send 5/10min, `onb_verify` anti-guessing 20/10min, `onb_claim`) is bypassable by rotating the header. Fix is in-repo: key on `x-real-ip`. Compounded by F2.

**F2 · MED ✔ — Rate limiter is in-memory / per-process.** `rebuildGuards.ts:186-188` (`ipBuckets = new Map`) and `app/lib/rateLimit.ts` are module-scoped; the code comments admit "multi-instance deploy would move this to Redis." On Vercel fan-out each instance has its own buckets → limits are largely non-functional even before F1. Together F1+F2 mean the onboarding rate limits provide little real protection.

**F3 · MED ✔ — Magic-link host poisoning via `x-forwarded-host`.** `app/lib/requestBase.ts:12` builds the emailed sign-in link base from `x-forwarded-host` with no allowlist; used at `onboarding/start/route.ts:55` to construct `${base}/api/onboarding/verify?token=<valid magic token>` and in `admin/invite` claim/sign-in URLs. If an attacker can set `x-forwarded-host` on the /start that generates a victim-addressed mail, the inbox link points at attacker.tld carrying a VALID signed token. **COULD NOT MEASURE:** whether Vercel strips/overwrites client-supplied `x-forwarded-host` before the function runs — if it does, impact drops to preview-dogfood only. Needs a platform check (or pin the base to an allowlisted host / `SITE_PUBLIC_URL` in prod).

**F4 · MED ✔ — `/api/onboarding/event` is unauthenticated AND unrate-limited.** `app/api/onboarding/event/route.ts` POST has no `rateLimit` and no auth (every sibling onboarding route imports rateLimit; this one does not) — just `req.json()` → `emitOnboardingEvent`. Anonymous caller floods unbounded telemetry rows (cost) and forges rows under any `onboardingId`/`error_code`, poisoning the friction report. Non-PII table, so bounded — but add a limiter + validate `onboardingId`.

**F5 · MED ✔ — SSRF via redirect-follow in content fetch.** `app/lib/contentView.ts:90,123` — `fetchRawHtml`/`fetchSiteContent` validate only the initial URL with the structural `validateUrl` (its own comment: "Does NOT resolve DNS"), then `fetch(..., {redirect:"follow"})`. The DNS-resolving guard `validateResolvedHost` EXISTS (`rebuildGuards.ts:156`) and is used in `api/rebuild/route.ts:161` — but NOT here. A `live_url` (or a host it 3xx-redirects to) pointing at `169.254.169.254`/`127.0.0.1` is fetched server-side and its body returned. Bounded — `live_url` is registry-sourced (not console-writable in this cluster) — but redirect-follow defeats the initial check regardless of source. Fix: call `validateResolvedHost` + `redirect:"manual"` re-validate.

**F6 · MED ✔ — Magic token + merchant email written to server logs (stub/relay path).** `app/lib/magicDelivery.ts:69-71` `console.log("[magic-delivery] STUB ... email=<${email}> url=${url}")` — the URL embeds the live single-use magic token. Fires only on the non-live operator-relay fallback (not when a real transport is wired); single-use/30-min. Still a bearer credential + PII in broadly-readable function logs. Redact the token (and email) or drop to a correlation id.

**F7 · MED (surfaced, not first-hand-traced) — /start email-bomb: claim consumed only at verify.** Per SA-1: `/start` re-resolves but does NOT redeem the claim (atomic redeem is in `onboardingFlow.ts:108`, at completeOnboarding), and `startOnboarding` accepts any plausible email → one leaked/shared claim token loops `/start` with arbitrary victim@ addresses → wingmen.dev-branded sign-in emails delivered to arbitrary inboxes (throttle spoofable per F1). **I did not end-to-end-verify the redemption timing myself** — recommend confirming before relying on it. If confirmed, consume/bind the claim earlier or cap sends per claim.

**F8 · MED→LOW ✔ — Unscoped store primitives (defense-in-depth gap, no DB-RLS backstop today).** Service-role (RLS-bypassing) functions with no ownership predicate: `sitesStore.ts:70-83 setSiteDeployment` (WRITE — vercel_project_id/live_url/status by `site_id` only), `sitesStore.ts:22-28 getSite` / `89-97 getSitesByIds` (metadata reads), `accountStore.ts:102-111 getAccountIdentity` (email+tg_chat_id by accountId). **Safe today only because every caller derives the id server-side** (deploy-signal signed target; prodOnboarding post-provision; handoff-verify behind bearer + signed baton). With the per-account DB-RLS path unwired (headline #2 — `withAccount()` has zero callers), one future caller passing client input = cross-tenant site-hijack / PII enumeration with no DB backstop. Add store-layer scoping or a `withAccount` path.

**F9 · LOW — Console link ROLE not enforced.** `api/console/edit/route.ts` + `change-request/route.ts` gate on `accountHoldsSite`/`getSiteForAccount`, which return true for a `site_links` row of ANY role; `role ∈ {owner,editor,viewer}` (`siteLinkStore.ts:14`) is never checked. A `viewer` can run draft edits / submit change-requests. Bounded (publish is a client-side stub; change-requests are human-reviewed). All 4 console routes DO enforce site ownership and 404 (not 403) — no existence leak (verified by SA-3).

**F10 · LOW — Infra detail leak (within trust boundary).** `deploy-signal/route.ts:153-154` returns up to 300 chars of the raw Vercel API body + projectId to the authenticated caller (holds `DEPLOY_SIGNAL_SECRET` = cc-finance). `opsAlert.ts:79-84` forwards the same infra detail to the operator Telegram allowlist. No secret/token is ever in these strings (verified: Vercel token only ever a Bearer header). Redact for hygiene.

**F11 · LOW — Timing oracle on length-mismatch.** Constant-time compares in `tg-issue.ts:35`, `handoff-verify.ts:40`, `admin/invite.ts:38`, `claim.ts:47` early-return on `a.length !== b.length` before the XOR loop → leaks secret/hash LENGTH via timing. Equal-length loop itself is fine. Minor.

**F12 · LOW — `admin/invite` `ttlDays` unvalidated.** `invite/route.ts:57` `Number.isFinite(body.ttlDays)` accepts negatives (immediately-expired) and unbounded large values (arbitrarily long-lived claim). Operator-only (bearer). Add bounds.

**F13 · LOW — No `server-only` build guard on service-key modules.** None of the 8 store modules imports the `server-only` package; `supabaseRest.ts` bakes `SUPABASE_SERVICE_KEY` into headers. If any is transitively imported by a Client Component, Next bundles the key into browser JS. Not observed in-cluster, but nothing enforces it. Add `import "server-only"`.

**F14 · INFO / DEPENDENCY ✔-adjacent — All forgery resistance hinges on `NODE_ENV==="production"` on EVERY reachable deploy (incl. preview).** `magiclink.ts`/`handoff.ts`/`session.ts` `getSecret()` returns the public `"dev-session-secret-change-in-prod"` default when not production. The PR#24 prod-guard throws when the secret is unset in prod — but a reachable deployment running with `NODE_ENV !== "production"` would forge magic links / handoff batons / session cookies, and the same flag governs the handoff PII gate. **COULD NOT MEASURE** NODE_ENV across preview/staging targets. Ties directly to the Gate-1 SESSION_SECRET item on #58.

### T1 verified-SAFE (named, per contract — not "clear" by assumption)
- **Money/entitlement gate FAIL-CLOSED:** `hasEntitlement`/`getEntitlements` return false/[] on missing-config/non-2xx/throw; `grantsForSku` deny-by-default on unknown sku; `grantEntitlements` fail-LOUD (throws, never silently grants). (`entitlements.ts`, `entitlementSku.ts` — SA-4, spot-checked.)
- **Deploy-signal authZ fail-closed:** 503 if `DEPLOY_SIGNAL_SECRET` unset, 401 on bad bearer (constant-time), dormant unless `PAID_VERCEL_ENABLED==="1"` + `livemode===true`; attach domain comes from operator `DEPLOY_SIGNAL_SLUG_MAP`, not the request → no SSRF; no shell exec.
- **No SQL/PostgREST injection:** postgres.js tagged templates only (no `sql.unsafe`); REST filter values wrapped in `encodeURIComponent`. GUC set parameterized (`shipforgeDb.ts:63`).
- **Console routes ALL enforce ownership** (edit/change-request/content/status → account-scoped `site_links`/`sites` query; 404 not 403).
- **No XSS sink:** React-escaped text; editor preview `iframe srcDoc sandbox` WITHOUT `allow-scripts`.
- **bot/ + pipeline/ clean:** `subprocess.Popen` list-form args (no `shell=True`), tokens from `os.environ` (no hardcoded secrets), no `eval`/`exec`.

---

## T2 — MIGRATION FINDINGS

**T2-A · ESCALATION (already routed) — `db/ceayj-tenant/*` are migrations against the LIVE ihsanos silo `ceayjeamtmcyzzvqflus`.** They provision a `shipforge` schema + scoped `shipforge_app` role + account-RLS inside prod ceayj (CAI-430/431 money-isolation). §6.6 applies. The files **already route correctly to cai/Nazim**: "DIRECT PSYCOPG only, NEVER `supabase db push`", dry-run first, cai residency-verify, cai FINAL confirm before any real-client PII, RUNBOOK CAI-432 "staged, NOT executed", Nazim-window-gated. **Action: do NOT apply on this convergence — cai/Nazim-owned, separate from GATE 2/3.** No further escalation needed from me; the gating is present and correct.

**T2-B · MED (schema drift — fix before the ceayj cutover) ✔ — ceayj target `shipforge.claim_tokens` is missing `invite_ref` + nullable `site_id`.** Substrate `public.claim_tokens` (`db/claim_tokens.sql`) carries op#11598's invite-scope shape (site_id nullable + `invite_ref` + `claim_tokens_scope_chk`), and the app **actively uses it** (`claimStore.ts:56` inserts `invite_ref`; `:86,:126` select it; verify/handoff routes carry `inviteRef`). But `db/ceayj-tenant/10_shipforge_tables.sql:247-257` defines `shipforge.claim_tokens` with `site_id text NOT NULL` and **no `invite_ref` column**. At RUNBOOK step-7/9 cutover (`SHIPFORGE_DB_URL`→ceayj.shipforge), invite-scoped onboarding breaks (NOT-NULL violation + missing column). Not a deploy blocker (ceayj path is cai-gated + dormant; today's path is REST→`public.*`), but the move-set must reconcile with op#11598 before cutover.

**T2-C · GOOD — idempotency + pinning.** All DDL idempotent (`CREATE IF NOT EXISTS`, `DROP POLICY IF EXISTS` before create, DO-guarded roles/constraints, idempotent `GRANT`). All SECURITY DEFINER functions pinned (`SET search_path = shipforge`) + `REVOKE ALL FROM PUBLIC` + scoped `GRANT EXECUTE` — matches the secure-RPC posture. No secrets committed (role password is a `<GENERATED_AT_APPLY_TIME>` placeholder). `30_substrate_decommission.sql` is irreversible `DROP TABLE … CASCADE` but guarded by an `app.decommission_ack` GUC precondition + RUNBOOK-last + cai FINAL.

**T2-D · LOW — apply-time parameters (present in RUNBOOK, flagging for the apply gate).** `00_` `GRANT CONNECT ON DATABASE postgres` assumes the ceayj DB is named `postgres` ("adjust if different"); the `<GENERATED_AT_APPLY_TIME>` password must be substituted before running Step 1; `accounts_tg.sql` must run after `accounts.sql` (bare `ALTER … DROP NOT NULL`, idempotent-in-effect but table-ordering-dependent). All are apply-checklist items, not code defects.

**T2-E · INFO — dual-home transition is a runtime/config switch, not a SQL defect.** `unlock_audit`/`ops_alerts`/`accounts`/`sites`/etc. exist in both `public.*` (substrate legacy — the LIVE REST service-role path today) and `ceayj.shipforge.*` (target — dormant). The load-bearing cutover is which store `SHIPFORGE_DB_URL`/`SUPABASE_URL` resolves to at runtime, correctly sequenced in the RUNBOOK.

**T2-F · CRUX VERIFIED ✔ — the ceayj account-RLS GUC is correctly wired app-side.** `shipforgeDb.ts:61-63 withAccount()` opens a transaction and sets `request.shipforge_account` **transaction-local** (`set_config(..., true)`), **parameterized**, from the verified session only; `sys()` is the no-GUC public/definer path. Matches the DB-side MONEY-4 invariant. The primitive is correct — but **UNWIRED: `withAccount()` has zero callers** (headline #2), so no per-account read/write is on this RLS path today. `sys()` (no account GUC) IS live in prod for `unlockAudit`/`opsAlert` only.

### Cross-track note for T4 (cc-shipforge's track)
`concierge/sushi-tei-sg.md` embeds a **real third party's contact data** (franchise@sushitei.com, head-office address/phone, real socials) plus a stable preview URL — corroborates orch-console's T4 SushiTei IP/impersonation concern. Surfacing so T4 connects it; not my track to adjudicate.

---

## COVERAGE / CAVEATS (could-not-measure, not "clear")
- **app/** fully read (4 subagents, MED+ re-verified by me). **db/** read in full by me. **bot/ + pipeline/ + concierge/** skimmed for egregious issues only (no line-by-line) — no shell-injection / hardcoded-secret found. **proxy.ts** not deep-read (F3/console 404 boundary references it; API routes are self-sufficient on their own ownership checks).
- Could-not-measure: (F3) Vercel `x-forwarded-host` stripping; (F14) NODE_ENV across preview/staging; (F7) claim-redemption timing not first-hand-traced; (F13) no build-graph proof that no Client Component imports a service-key module (grep found no `server-only` guard); `looksLikePii()` efficacy + limiter store internals not verified.
- This review is of MERGED code with **GATE 0 not given**. Nothing here authorises a deploy or a migration apply.

— cc-quality
