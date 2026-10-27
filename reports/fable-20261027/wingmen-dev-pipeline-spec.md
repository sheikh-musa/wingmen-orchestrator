# wingmen.dev site pipeline: implementable build spec

*Fable 5.1 temporary lane (cc-substrate-4), 2026-10-27, for Musa op#34986-34990 (run authorised op#34969-34972). Base: `reports/truhint-teardown-20261026/TEARDOWN.md` (live checkout), section 2 read in full. Status: spec only. Nothing built, merged or deployed. Written so an engineer lane can execute it without coming back to the author; every "DECISION" line is Musa's call and has a recommended default so the lane is never blocked.*

---

## 0. What we are building, in one paragraph

A done-for-you website service on wingmen.dev that copies the truhint packaging (free bespoke preview, pay after approval, client owns everything, a care ladder) and beats it on four axes: openly AI-built, cheaper per unit, minutes not days, and genuinely different design per site. The flow is intake (web form or Telegram) → automatic enrichment from the prospect's public footprint → bespoke generation under a design-DNA sampler with an anti-sameness check → live preview on wingmen.dev, released only after an Anthropic-routed eyeball passes it → self-serve approve and pay → automatic multi-page build and deploy to the client's own Vercel project → care by a dedicated per-client Telegram channel where edits land as commits and redeploys in minutes. Client site content never enters a shared database: it lives in the client's own repo and Vercel project. Leads and pipeline state live in one dedicated store that is provisioned and registered before the first write.

Unit economics already measured (`docs/strategy/2026-07-05-shipforge-pricing-pressure-test.md`): about US$0.45 per multipass generated site, US$0.03 per routed chat edit, fixed base about US$79 a month. Nothing in this spec changes those numbers except the eyeball gate, costed in §5.

---

## 1. Architecture

### 1.1 Stage map

| # | Stage | Surface | Runs where | Produces | Reuses (see §7) |
|---|---|---|---|---|---|
| 1 | Intake | wingmen.dev `/start` form; Telegram bot `@wingmendevbot` conversational intake | Vercel (form) + Mac Mini or VPS bot process | a `leads` row with a structured brief | shipforge `app/api/onboarding/*`, `bot/` long-poll loop |
| 2 | Enrichment | worker job | worker (Mac Mini now, VPS later) | `brief.md` + `brief.json` + screenshots of the existing site, palette from logo and photos, hours and services from public pages | `pipeline/capture.py`, `pipeline/from_instagram.py`, `pipeline/screenshot.js` |
| 3 | Bespoke generation | worker job | worker, Anthropic-routed design model | a complete Next.js site in a per-lead build directory, a `design_dna` record, cost record | `pipeline/rebuild.py`, `app/lib/rebuildPipeline.ts`, frontend-design skill, brandit two-pass pattern |
| 4 | Pre-checks + eyeball gate | worker + reviewer lane | automated checks on worker; verdict by an Anthropic-routed reviewer | `qa_verdicts` row keyed by content hash; PASS or FAIL with notes | `nervous_system/ihsan_gate.py`, deploy-gate verdict-by-hash pattern |
| 5 | Live preview | Vercel project `wingmen-previews` | Vercel | unguessable preview URL `wingmen.dev/p/<token>`; released to the prospect only on PASS | `scripts/deploy-preview.sh`, `app/preview/*`, `publish_share.sh` slug entropy |
| 6 | Approve + pay | wingmen.dev `/p/<token>/approve` → Stripe Checkout | Vercel + Stripe | `orders` row, Stripe customer and subscription ids, triggers build | `app/api/unlock` shape (stub today), `app/lib/entitlements.ts` |
| 7 | Build + deploy | worker job | worker → GitHub → Vercel | client repo `site-<slug>` created from the approved artefact, multi-page expansion, Vercel project `site-<slug>`, DNS instructions | `pilots/` one-Vercel-project-per-client convention, `scripts/export_tenant.mjs` for the export |
| 8 | Care | dedicated Telegram bot per client | bot process + worker | chat edit → structured patch → diff → Confirm → commit to the client repo → Vercel auto-deploy | `bot/manage_bot.py`, `bot/nlmap.py`, `bot/patcher.py`, `scripts/onboard_client_channel.py`, TG2.0 design |
| 9 | Content ops (later) | scheduled worker jobs | worker | SEO articles, Google Business Profile posts, facts file and JSON-LD refresh | generator emits schema and facts file by default |

### 1.2 Repos and services

| Name | Kind | Purpose | Notes |
|---|---|---|---|
| `sheikh-musa/wingmen-sites` | NEW repo, Next.js, Vercel project `wingmen-dev` (domain wingmen.dev) | marketing pages, vertical landing pages, intake form, preview host routes, approve and pay, thin client console | Port from `~/wingmen/projects/shipforge-deployed/app` (claim, console, onboarding, preview, api). Drop `/api/rebuild` public proxy; generation is triggered by intake, not by a public URL box. |
| `sheikh-musa/wingmen-sites` `worker/` + `pipeline/` | Node worker + Python pipeline inside the same repo | enrichment, generation, pre-checks, build, deploy, care commits | Port `worker/server.mjs`, `run-worker.sh`, launchd plists, `pipeline/*`. Replace the cloudflared quick tunnel with a Tailscale-reachable worker or move the worker to the VPS. |
| `sheikh-musa/wingmen-sites` `bot/` | Python | intake bot and care bots (one process, many tokens) | Port `bot/manage_bot.py`, `nlmap.py`, `patcher.py`; swap `store.py` (Supabase `site_content` row + revalidate) for a git-commit backend. |
| `sheikh-musa/site-<slug>` | one repo per client, Next.js static export or App Router, Vercel project `site-<slug>` | the client's actual site: `content/site.json`, `content/pages/*.json`, `public/assets/*`, generated components | Private repo, client gets collaborator access and a zip export on request. This is "client owns everything". |
| `wingmen-previews` | one Vercel project | preview deployments before approval | each preview is a deployment aliased to `p-<token>.wingmen.dev`; purged 30 days after last view if not converted. |
| Stripe account (Wingmen) | SaaS | checkout, subscriptions, invoices | Test mode until Phase 5. |
| GitHub App `wingmen-sites-bot` | GitHub App with repo-create and contents scopes on the `sheikh-musa` account (or a dedicated org) | creates client repos, commits care edits | Keeps the fleet's personal admin token out of the worker. |
| Reviewer lane `cc-sites-eyeball` | Anthropic-routed Claude Code lane on a Max pool with image reading | the eyeball gate (§3) | Can be cc-quality itself if capacity allows; must never be a GLM or DeepSeek lane. |
| Engineer lane `cc-sites` | builder lane | executes this spec | Boot with `scripts/seed_lane_claude_md.py`; CLAUDE.md shaped like `~/wingmen/projects/bariq/CLAUDE.md`. |

### 1.3 Data classes and exactly where each lives (residency design)

Rule applied (TENANT-RESIDENCY-001, LAYER-VOCAB-001): name the store and the project ref for every class; no client rows into a shared project before its store is designated.

| Data class | Examples | Store | Ref | Why |
|---|---|---|---|---|
| Fleet coordination | bus rows, asks, lane registry, `bot_channels` routing config (no message content) | orchestrator substrate | `tscuymavysscrvoberrr` | unchanged; never holds site content or prospect PII |
| Leads and briefs | prospect business name, contact email and phone, industry, intake answers, enrichment output, preview token, status | **`wingmen-sites` store, NEW dedicated Supabase project, ap-southeast-1 (Singapore)** | ref assigned at provisioning; registered in `docs/data-store-registry.md` before the first intake write | prospect PII is Wingmen's own commercial data (like COSEM receivables was ruled commercial data); one small dedicated project keeps it out of ceayj's money and buyer-PII tables and out of the substrate |
| Site registry and pipeline state | `sites` (slug, repo, vercel project, plan, care tier, domain), `pipeline_runs`, `design_dna`, `qa_verdicts`, `cost_events`, `care_edits` (metadata only: who, when, diff hash, not content) | `wingmen-sites` store | same | operational metadata, no site content |
| Payments | Stripe customer id, subscription id, invoice ids | `wingmen-sites` store, plus Stripe | same | card data never touches Wingmen |
| Client site content and assets | copy, images, menus, prices, hours, pages, generated components | **client repo `site-<slug>` + Vercel project `site-<slug>`** | n/a (git + Vercel, one per client) | residency by construction: each client's content is physically separate, exportable as a repo zip, deletable by deleting the repo and project; no shared database holds it |
| Preview artefacts before approval | generated site for a lead that has not paid | worker build directory + `wingmen-previews` deployment, referenced from the lead row | n/a | prospect-supplied and public data only; purged on the 30-day rule |
| Care chat content | the client's Telegram messages | Telegram servers; the bot keeps only the structured patch and the diff hash in `care_edits`; raw message text is not persisted | n/a | the TG2.0 model: per-client dedicated token and channel; no message log in a shared table |
| Generation logs | token counts, model, cost per run | `wingmen-sites` store `cost_events` and the worker's append-only `logs/costs.jsonl` | same | the pressure-test instrumentation, kept |

**DECISION (Musa): the store for leads and pipeline state.** Recommended default: provision a new Supabase project `wingmen-sites` in ap-southeast-1 under the Wingmen org, register it, and point every pipeline write at it. The alternatives are (a) an RLS-isolated tenant inside the ihsanos multi-tenant DB `ceayjeamtmcyzzvqflus`, which is where Bariq's site content was pinned on 2026-10-26 (op#34118) and where the shipforge plan `docs/shipforge-silo-migration.md` (CAI-RESP-430) wanted to go, or (b) a per-client Supabase project for every client. (a) puts a public-facing intake surface next to storefront money tables and the silo-migration doc itself explains why a shared JWT secret makes that isolation weaker than it looks; (b) is cost and admin overhead for sites that hold no database content at all. The default costs about US$25 a month and removes the question for every future client, because client content never enters any database. If Musa prefers (a), nothing else in this spec changes except the connection string and the registry row.

**DECISION (Musa): Bariq.** Bariq's content is currently pinned to the shared ihsanos DB. Under this spec it moves into `site-bariq` repo + Vercel project when Bariq becomes the first real client in Phase 5. That is a residency change and needs Musa's word; recommended default is to move it, because it makes Bariq identical to every later client.

### 1.4 Identity and access

- The worker holds: the `wingmen-sites` service key, the GitHub App private key, the Vercel token for the `wingmen-aa9356e1` scope, Stripe secret, Anthropic key for generation. It never holds ceayj, goumlyne, substrate write, or any client silo credentials. Enforce with the env-shadow audit pattern already recorded for shipforge (process env must not ambiently carry other tenants' secrets).
- Care bots: one Telegram bot token per client, rendered and validated by `scripts/onboard_client_channel.py` (dedicated-token policy, bus #71813). A client's bot can only commit to that client's repo (GitHub App installation scoped per repo).
- Preview tokens: 10 hex chars of entropy, same as `publish_share.sh` auto slugs; previews are `noindex`.

---

## 2. The uniqueness engine

The failure mode to design against is the one the teardown names: every site is recognisably the same layout kit with swapped content. "Unique" is defined operationally and measured, not asserted.

### 2.1 Inputs

1. **Brief** (from intake + enrichment): business type, money action (call, WhatsApp, book, order, directions), services, hours, locale and language, tone words chosen by the prospect (from a 12-word list plus free text), real photos and logo, existing site capture if any (`capture.py` integrity rule: missing sections are recorded as absent, never invented).
2. **Vertical primitives**: the content sections a vertical needs (the truhint per-trade reorderings), kept as data, not layouts.

### 2.2 Design DNA sampler

For each lead the worker samples and records a `design_dna` object:

| Axis | Options | Source of variation |
|---|---|---|
| Layout archetype | 12 named archetypes (editorial split, asymmetric grid, poster hero, long-scroll narrative, magazine columns, brutalist blocks, framed specimen plates, diagonal rhythm, stacked cards-free, typographic hero, photo-first bleed, menu-board) | sampled with weights by vertical, excluding the archetypes used by the last 10 shipped sites in that vertical |
| Type system | about 40 curated pairings (display + text + mono) from Google Fonts, each with scale and tracking rules | sampled excluding pairings used by the last 20 sites; brand fonts override |
| Colour system | derived from the brand's real assets (logo and photo palette extraction) → a generated scale; if no assets, from the tone words | derived, so it is different per business by nature; a hue-distance check against the corpus catches coincidences |
| Imagery treatment | duotone, halftone, crop-mark plates, grain, hard crops, full bleed, polaroid, none | sampled |
| Motion grammar | 4 grammars (restrained fade, staggered rise, parallax-light, none) with a hard performance budget | sampled, mobile-safe |
| Section order | the vertical primitives reordered around the money action | derived from brief |
| Micro-detail set | dividers, numerals, labels, corner radius family, border language | sampled |

The DNA is hashed and stored. The generator receives the brief plus the DNA and writes the components; it does not pick from pre-built page layouts. The house library contains primitives only (buttons, forms, map embed, hours block, WhatsApp button, nav patterns), never whole sections or pages.

### 2.3 Two-pass generation (the pattern already proven on Brandit)

1. **Faithful pass** (only when the prospect has an existing site): capture and reproduce content and structure to parity; tag it `faithful-baseline`. This is the content truth and the comparison build (`~/wingmen/projects/brandit-faithful`, tag `faithful-baseline`).
2. **Elevated pass**: the design model writes the bespoke site from brief + DNA, keeping content and section order from pass 1, changing every design decision. The Brandit "kopitiam print shop" result is the quality reference.

Generation model: Anthropic-routed (the operator rejected a dedicated cloning model in June; the lever is the pipeline). Multipass scripted generation (plan → components → polish) measured at about US$0.45. GLM or DeepSeek lanes may run the deterministic parts (capture, build, deploy, checks) but not the design pass.

### 2.4 Anti-sameness check (fail-closed before the eyeball)

A preview is rejected and rerolled with a fresh DNA sample if any of these fail:

| Metric | How | Threshold (starting values, tune after Phase 1) |
|---|---|---|
| DNA distance | weighted Hamming distance over the axes above against every site in the same vertical and the last 20 overall | must differ on at least 4 of 7 axes from every corpus member |
| Perceptual distance | pHash and a CLIP-style embedding of the 1440 and 390 full-page screenshots vs the corpus | cosine distance above 0.25 to the nearest neighbour; pHash Hamming above 18 |
| Structural distance | tree edit distance over the section skeleton (tag + role sequence, no text) | below 0.7 similarity to any corpus member |
| Hero distinctness | the above-the-fold crop alone must pass perceptual distance | same thresholds |

All four are computed by the worker, stored on the `qa_verdicts` row, and shown to the reviewer. Rerolls cost US$0.15 to 0.45 each; cap at 3 before a human is asked.

### 2.5 Measuring it over time

- **Weekly line-up test**: six of the most recent previews side by side at 390 width. The reviewer must not be able to pick a shared kit; if they can, the DNA axes are too narrow and the library grows.
- **Nearest-neighbour report** per site in the console (which earlier site it is closest to and by how much), so drift toward a house look is visible.
- **Target**: median nearest-neighbour perceptual distance rising, not falling, as the corpus grows.

---

## 3. The quality gate (eyeball on every first preview)

GLM-routed lanes cannot judge PNGs (recorded fleet finding). So the gate is Anthropic-routed and fail-closed in code.

### 3.1 Automated pre-checks (worker, before any eyeball)

1. Lighthouse mobile, throttled: performance ≥ 90, accessibility ≥ 95, best practices ≥ 95, SEO ≥ 95.
2. axe: zero serious or critical issues.
3. Layout: `document.scrollWidth` = 390 at mobile; no horizontal overflow at 1440.
4. Content fidelity: every factual claim on the page traces to the brief or the capture (a claims list is emitted by the generator; a checker verifies each against brief.json); no placeholder text; no invented testimonials, clients, awards or credentials.
5. Links and forms: every link resolves; contact actions use the prospect's real channels.
6. Assets: images optimised, video poster and `preload="none"`, total transfer under the budget.
7. Anti-sameness (§2.4).

### 3.2 The eyeball

- Reviewer: `cc-sites-eyeball` (or cc-quality), Claude on a Max pool with image reading. Input: 1440 and 390 full-page PNGs, above-the-fold crops, the DOM assert report, the brief, the DNA record, the nearest-neighbour report.
- Rubric (1 to 5 each, PASS requires every item ≥ 4 and "distinctive" ≥ 4): distinctive (not a kit), hierarchy and readability, type craft, colour and contrast, mobile composition, brand fit to the brief, motion restraint, overall "would a client pay for this".
- Output: `reports/sites/<content_hash>/eyeball-review.md` committed in the pipeline repo plus a `qa_verdicts` row (content hash, verdict, scores, notes, reviewer identity, model).

### 3.3 Enforcement in code, not by promise

- The "release preview to prospect" action (`POST /api/preview/release`) refuses unless a `qa_verdicts` row with `verdict='PASS'` exists for the exact content hash of the deployed preview. Same shape as the console deploy gate that reads a committed review by content hash.
- Any change to the preview (reroll, edit) changes the hash and re-arms the gate.
- A config value `eyeball_mode` ∈ {`every_first_preview`, `sample_1_in_5`} lives in the `wingmen-sites` store, defaults to `every_first_preview`, and may only be flipped by orch-console after 20 consecutive previews with a first-pass PASS rate of 80 percent or better. Care edits that only change text within an existing section skip the eyeball; any structural or visual edit re-arms it.
- Human sample audit: Musa or the website partner looks at one in five PASSed previews during Phase 1 and 2; their disagreement rate is tracked. If it exceeds 20 percent the rubric is revised, not the mode.

### 3.4 Cost of the gate

One review is about 25k input tokens with images and 2k output; on a Max pool it is capacity, not cash. On metered API at Sonnet rates it is roughly US$0.10 per review, immaterial against a US$299 build.

---

## 4. Phased milestones, each with a demoable exit test

| Phase | Scope | Exit test (demoable) | Client data |
|---|---|---|---|
| **0. Foundations (2 to 3 days)** | Musa's store decision applied: `wingmen-sites` project provisioned and registered; `wingmen-sites` repo scaffolded from shipforge-deployed with the public rebuild box removed; schema (`leads`, `sites`, `pipeline_runs`, `design_dna`, `qa_verdicts`, `cost_events`, `orders`, `care_edits`) applied with the direct-apply migration pattern; worker runs on the Mini with cost logging; reviewer lane booted; DNA library v1 (12 archetypes, 40 type pairings) checked in as data | `make doctor` green: store reachable with the pipeline role only, worker health endpoint up, a dry-run generation of one synthetic brief produces a build directory and a DNA record with zero network calls to any client store | none |
| **1. Bespoke previews for three businesses (week 1 to 2)** | Enrichment + two-pass generation + anti-sameness + pre-checks + eyeball gate + preview deployment, driven by hand-written briefs | Three live preview URLs on `wingmen-previews`, zero client data: (1) coffeemedia (operator-owned brand, real public content), (2) a synthetic halal cafe, (3) a synthetic employment agency shaped like Bariq but invented. Each has a committed PASS verdict, Lighthouse mobile ≥ 90, anti-sameness metrics above threshold against the other two and against the Brandit elevated build, a logged generation cost ≤ US$2 per preview including rerolls, and a side-by-side line-up PNG in which the reviewer could not identify a shared kit. Demo: open the three links and the line-up on a phone | none (synthetic or operator-owned) |
| **2. Intake to gated preview, hands-off (week 3)** | Web form and Telegram intake, brief validation, automatic enrichment, generation queue, preview release gate | Submit the web form for a synthetic business and receive a preview link in the confirmation channel within 15 minutes with no human touching the pipeline other than the reviewer's verdict; repeat via the Telegram intake bot; a FAIL verdict visibly withholds the link | synthetic |
| **3. Approve, pay, build, deploy (week 4 to 5)** | Approve page, Stripe Checkout in test mode, GitHub App repo creation from the approved artefact, programmatic service and town pages, Vercel project per site, custom-domain instructions, PWA manifest and JSON-LD and facts file emitted by default, repo zip export | A test-mode payment on a synthetic lead produces a deployed multi-page site on a new `site-<slug>` Vercel project within 60 minutes, the export zip rebuilds and runs locally, and the lead row shows the full state machine from `new` to `live` | synthetic |
| **4. Care by Telegram (week 5 to 6)** | Per-client dedicated bot channel rendered with `onboard_client_channel.py`, chat edit → structured patch → diff → Confirm → commit → Vercel deploy, text-only edits skip the eyeball, structural edits re-arm it | In the synthetic client's channel, "change opening hours to 9 to 6" is live within 5 minutes with a before-and-after diff; a two-client isolation run (reuse the 18-case pattern from the shipforge console) shows no cross-channel or cross-repo effect | synthetic |
| **5. First real client and prices live (week 7 onward)** | Residency pin for the client (default: own repo + Vercel project; Bariq moves if Musa agrees), Stripe live, published prices, vertical landing pages for the first two verticals, founder pricing for the first ten | One real client's site live on their domain with care active, paid through Stripe, and a weekly report in the console; then open the 20-preview conversion test from the business-ideas report | real, under its designated store |
| **6. Content ops (after Phase 5 proves)** | Scheduled SEO articles, Google Business Profile posting, review-reply drafts, facts-file refresh | One month of scheduled content for one client, drafted, approved in the client channel, published | real |

Every phase ends with a bus update to orch-console carrying the exit-test evidence (links, PNGs, verdict paths). No phase touches production domains without Musa's word, same rule as the Bariq and coffeemedia lanes.

---

## 5. Recommended defaults for the eight open questions (teardown §2.4)

1. **Pricing and packaging.** Copy the ladder: one-time build plus monthly care; hold roughly truhint's price points in SGD (Starter S$399 for up to 3 pages, Growth S$1,299 for up to 12, Pro custom) and win on unlimited conversational edits, speed and bundled PWA and schema rather than on a lower sticker; care at S$39, S$69 and S$129 a month with no edit cap and a fair-use throttle at 100 structural edits a month; founder price minus 30 percent for the first ten clients in exchange for a testimonial.
2. **The call.** Kill it as a default; async feedback on the preview via Telegram or the web; a 15-minute call is a perk of the Pro tier and of any regulated-vertical lead.
3. **Uniqueness bar and QA.** "Bespoke enough" = passes the four anti-sameness metrics in §2.4 and scores distinctive ≥ 4 from the reviewer; every first preview is eyeballed by an Anthropic-routed reviewer until 20 consecutive previews pass first time at 80 percent or better, then one in five, flipped only by orch-console.
4. **First verticals.** Singapore SME service businesses whose money action is WhatsApp or a call: cafes and small F&B, employment agencies, home services, clinics; build vertical landing pages for cafes and agencies first because coffeemedia and Bariq are the real instances.
5. **Residency.** Leads and pipeline state in a new dedicated `wingmen-sites` Supabase project in Singapore, registered before the first write; client content in the client's own repo and Vercel project, never in a shared database; Bariq migrates to that shape when it becomes the first paying client. Musa's decision, recorded in the registry.
6. **Scope at launch.** Generation, gated preview, approve and pay, build and deploy, Telegram care. PWA, JSON-LD and the facts file are free because the generator emits them. SEO articles, Google Business Profile automation and review replies are Phase 6.
7. **Payments and domains.** Own Stripe Checkout and subscriptions (test mode until Phase 5). The client registers the domain in their own name; the pipeline gives exact DNS records and verifies them through the Vercel domains API. No registrar reselling.
8. **Scale target.** Ten sites a month through Q4 2026 to prove the engine (every preview eyeballed), forty a month by Q2 2027 (one-in-five sampling). The eyeball budget is derived from this number, not the other way round.

---

## 6. State machine, schema sketch and interfaces (so the lane does not have to invent them)

**Lead states:** `new → enriched → generating → precheck_failed | awaiting_eyeball → eyeball_failed | preview_ready → preview_sent → feedback → regenerating | approved → paid → building → live → care_active | churned | purged`.

**Tables in the `wingmen-sites` store** (names only; the lane writes the DDL under `migrations/` and applies it with the direct-psycopg pattern, never `supabase db push`):
`leads`, `briefs`, `enrichments`, `pipeline_runs`, `design_dna`, `previews`, `qa_verdicts`, `orders`, `sites`, `domains`, `care_channels` (channel key and bot token env name only, no token), `care_edits`, `cost_events`, `ops_alerts`.
All tables carry `lead_id` or `site_id`, `created_at`, and an append-only `events` companion for audit. Row-level security: the pipeline role can write; the console role is read-only; no anon access.

**Worker API (internal, bearer-authenticated, reachable over Tailscale, re-validating every URL against the SSRF list from `app/lib/rebuildGuards.ts`):**
`POST /jobs/enrich {lead_id}`, `POST /jobs/generate {lead_id, dna_override?}`, `POST /jobs/precheck {run_id}`, `POST /jobs/preview {run_id}`, `POST /jobs/build {order_id}`, `POST /jobs/care-edit {site_id, patch}`, `GET /health`, `GET /costs`.

**Bot contract (care):** message → `nlmap` → `{section, path, value}` patch or a decline with reason → diff rendered → Confirm or Cancel keyboard → on Confirm, commit to `site-<slug>` via the GitHub App → poll Vercel deploy status → reply with the live URL and the diff. Only the client's chat ids in `bot_channels.group_routing` are honoured.

**Generator contract:** input `brief.json` + `design_dna.json` + vertical primitives; output a Next.js project in the build directory plus `claims.json` (every factual statement with its source pointer) and `dna.lock`. The faithful pass, when it runs, is kept under `baseline/` for the structural comparison.

---

## 7. Existing fleet code that is reusable (paths verified present on 2026-10-27)

**In `~/wingmen/projects/shipforge-deployed` (the live shipforge checkout; `~/wingmen/projects/shipforge` is the same code without the pilots):**

- `pipeline/capture.py`, `pipeline/rebuild.py`, `pipeline/from_instagram.py`, `pipeline/screenshot.js`, `pipeline/README.md`: URL → brand brief with the integrity rule (absent sections recorded, never fabricated), 1440 and 390 screenshots, Instagram ingestion. Stage 2 enrichment is this, almost unchanged.
- `app/lib/rebuildPipeline.ts`, `app/lib/rebuildGuards.ts`, `app/lib/rateLimit.ts`: the orchestration shape, SSRF guard list, rate limiting. Port the guards verbatim.
- `worker/server.mjs`, `worker/run-worker.sh`, `worker/run-tunnel.sh`, `worker/launchd/*.plist`, `worker/README.md`: the Mac Mini worker with bearer auth, concurrency cap, NDJSON progress and the append-only `logs/costs.jsonl` cost log. Keep the worker, drop the quick tunnel.
- `bot/manage_bot.py`, `bot/nlmap.py`, `bot/patcher.py`, `bot/store.py`, `bot/channel.py`, `bot/test_*.py`: natural language → structured patch → diff → Confirm-before-write, with unit tests. Swap `store.py` for a git-commit backend.
- `app/claim/*`, `app/console/*`, `app/api/onboarding/*`, `app/api/console/*`, `app/lib/claim.ts`, `app/lib/magiclink.ts`, `app/lib/telegramDelivery.ts`, `app/lib/session.ts`, `app/lib/onboardingFlow.ts`: self-serve onboarding with one-time claim links, email magic link or Telegram deep link, signed cookie session, least-privilege console (404 not 403), and the 18-case multi-client isolation test that passed in July.
- `app/lib/entitlements.ts`, `app/lib/entitlementSku.ts`, `app/api/unlock/route.ts` (a stub today), `app/lib/unlockAudit.ts`: the shape of approve-and-pay and the audit row; wire to Stripe.
- `db/sites.sql`, `db/site_links.sql`, `db/accounts.sql`, `db/accounts_tg.sql`, `db/claim_tokens.sql`, `db/change_requests.sql`, `db/onboarding_events.sql`, `db/ops_alerts.sql`, `db/unlock_audit.sql`: schema to port into the `wingmen-sites` store. `db/ceayj-tenant/*` shows the tenant-in-ceayj alternative with its isolation assertions.
- `scripts/deploy-preview.sh` (preview deploy with a pinned alias, never prod), `scripts/export_tenant.mjs` (per-tenant export, the basis of "client owns everything"), `scripts/synthetic_gate.mjs`, `scripts/mint_claim.mjs`, `scripts/seed_store.py`.
- `docs/shipforge-silo-migration.md`: the residency reasoning, including why a shared JWT secret defeats tenant isolation via PostgREST; cite it in the store decision memo.
- `pilots/` (one directory per client, one Vercel project per client, untracked by convention): the per-client Vercel project pattern that becomes `site-<slug>`.
- `STATUS.md`: handoff convention and the open PRs #5 and #7 that should be closed rather than carried.

**In the orchestrator repo (`~/wingmen/orchestrator`, this worktree mirrors it):**

- `scripts/onboard_client_channel.py`: renders and validates a gated `bot_channels` migration for a new client Telegram channel with the dedicated-token policy and pin consistency checks; the care-channel provisioning step uses it as is. Design: `reports/tg2.0-client-onboarding-design-20261017/design.md` (live checkout).
- `scripts/lane_reply.sh`, `scripts/stage_client_file.py`: lane-side reply and client-file staging patterns for the care bot's human-escalation path.
- `nervous_system/ingest.py`, `nervous_system/tg_out.py`, `nervous_system/operator_log.py`: the unified Telegram ingest and outbound gateway the intake bot should register with rather than long-polling on its own.
- `scripts/publish_share.sh` and `~/wingmen/wingmen-share` (`middleware.js`, `app/docs.json`): per-tenant password-gated document host with high-entropy slugs; use for proposals and the line-up PNGs, and copy the slug entropy for preview tokens.
- `docs/ihsan-gate-manifest.yaml`, `nervous_system/ihsan_gate.py`: the machine-readable six-check gate; the pre-check list in §3.1 maps onto it.
- `scripts/deploy_console.sh` (and `scripts/git-hooks/pre-push`): the fail-closed "verdict by content hash" gate that §3.3 copies.
- `scripts/bus_send.py`, `scripts/seed_lane_claude_md.py`, `templates/lane_claude_template.md`, `scripts/lanes.sh`, `scripts/launch_dangerous_cc.sh`, `scripts/self_recycle.sh`: booting and running the engineer and reviewer lanes.
- `scripts/apply_migration.py` and `docs/data-store-registry.md`: how the new store's migrations are applied and where the new store is registered before the first write.

**Site builds already done on this stack (quality references and real instances):**

- `~/wingmen/projects/brandit-faithful` (`README.md`; `main` = elevated "kopitiam print shop" design, tag `faithful-baseline` = pixel-faithful clone; DOM asserts 54 of 54; `/proof` screenshots): the two-pass method and the quality bar.
- `~/wingmen/projects/coffeemedia` (`CLAUDE.md`): the operator-owned rebuild, Phase 1 business number one.
- `~/wingmen/projects/bariq` (`CLAUDE.md`, `web/`): the first real external site and the first per-client Telegram channel test; preview-only rule.
- `~/wingmen/projects/wordpress-sites` (`README.md`): Layer-1 WordPress import to `migration_package_v1`, for prospects on WordPress.

Not reusable and should not be revived: the shipforge brand and domain, the public "paste a URL" rebuild box as the front door, the cloudflared quick tunnel, the `site_content` row plus revalidate edit path (replaced by commits to the client repo).

---

## 8. Risks the lane must respect

- **Design judgment cannot run on cheap models.** Generation and the eyeball stay Anthropic-routed; everything deterministic can run anywhere.
- **No invented facts.** The claims checker is a hard gate; a beautiful site with a made-up testimonial is a failure.
- **Residency before the first write.** Phase 0 does not end until the store is registered and the worker's env carries only the credentials in §1.4.
- **Production domains need Musa's word.** Every phase through 4 is previews and synthetic data; Phase 5's first real domain is an explicit go.
- **Eyeball mode is not a lane's to flip.** Only orch-console flips `eyeball_mode`, with the 20-preview evidence.
- **Cost logging stays on.** Every generation, reroll and review writes a `cost_events` row so the pressure-test numbers keep being measured, not remembered.

*End of spec.*
