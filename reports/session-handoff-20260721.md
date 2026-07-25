# Session handoff — 2026-07-21 (cc-orchestrator / hub)

Enormous full-tilt session: shipped the Dookana marketplace MVP + back-nav, launched Bayan (Al-Mīzān rebrand + IASKUSTAZ partnership), deployed cosem-tdu, device-photo upload, a polish batch — and hit a hard, humbling client-bot incident. Booted from a prior /clear at ~98%; now checkpointing at 99%.

## ⚠️ PENDING OPERATOR INSTRUCTION (open thread — DO THIS)
**Report the scholar EVAL PASS RATE to the operator when cc-scholar is done.** cc-scholar is building a retrieval-quality eval seeded from the 136 real `mizan_interactions` queries. When it reports the grade breakdown (good / correct-gate / honest-not-found / BAD across the 136), relay the pass rate to the operator on @ihsanos. AND — per the operator's "this is not ihsan" + [[test-end-to-end-before-declaring-live]] — **independently verify a real `ayat kursi tafsir` answer myself** (read the actual persisted reply / send a real message) before telling him anything is "fixed." Do NOT round up to "works."

## THE HOT ITEM: Bayan bot quality (cc-scholar active)
Client-facing religious Q&A bot **@bayanQAbot** (renamed from Al-Mīzān). Two "not ihsan" failures this session:
1. **Delivery — FIXED** by cc-scholar: the bot was polling the OLD `@mzninterfacebot` token (a token reference the `.env` swap missed), so it never consumed `@bayanQAbot` messages → dead bot. **It reached Nabil (the ustaz) dead** — the operator was rightly upset. Root cause found + fixed; bot now responds. (My earlier IPv6-no-route diagnosis was a real but secondary issue; IPv4 fix committed `e03c39f`.)
2. **Retrieval quality — IN PROGRESS**: `"ayat kursi tafsir"` returned only a cross-reference (Ibn Kathīr's Āl-'Imrān opening that *names* the verse), NOT the dedicated 2:255 tafsir that *explains* it. Named-verse alias ("ayat/ayatul kursi") isn't resolving to surah:ayah 2:255 in the TAFSIR retrieval path (the verse-lookup path resolves it fine). The bot's *honesty* is intact (it admitted a cross-reference, didn't fabricate) — the discipline works; retrieval is the bug.

**cc-scholar tasks dispatched (bus 10546/10550/10551/10552):** fix named-verse tafsir retrieval (reuse `_build_surah_aliases`); **BUILD a retrieval EVAL seeded from the 136 real distinct `mizan_interactions` queries** (190 rows total; grade each good/gate/honest-not-found/BAD; BADs = regression set + standing pre-deploy gate — the ihsan-factory fix for reactive whack-a-mole); HARD e2e gate: do NOT report done until a real `@bayanQAbot` message gets a real good answer, reported with the actual reply text.
- Bot infra: `dev.wingmen.mizan-bot` launchd on **Studio**, runs from `~/wingmen/projects/ai-scholar` (main `e03c39f`). Token `MIZAN_BOT_TOKEN` in `ai-scholar/.env` = @bayanQAbot (`8916853779:AAG8...`). `.env.bak-bayan-cutover` holds the OLD @mzninterfacebot token (revert path). Restart: `launchctl kickstart -k gui/$(id -u)/dev.wingmen.mizan-bot`.
- **Bayan poll-loop hardening** still owed (webhook-on-shutdown fights launchd polling; robust getUpdates timeout). An `al-bayan-bot` Edge Function is deployed (POST 200) — a SEPARATE impl; if ever switching production to the webhook, verify it carries the 6 fixes + the @bayanQAbot token first.

## SHIPPED THIS SESSION (live, both silos ceayj+goumlyne unless noted)
- **Marketplace MVP** (ihsanos): slice 1 browse/search · slice 2 location + nearby map (encrypted address in `organization_location`, server-fuzzed ~1km coarse geo, post-accept reveal) · slice 3 merchant "Get Listed" guided flow · food-photo StoreCard polish · customer/merchant role entry-points (role-as-context, `org_members`).
- **Back navigation everywhere** (native TG BackButton + web fallback).
- **cosem-tdu deploy** — 47-commit backlog live on tdu-tools-prod.web.app (Firebase, via `firebase-hosting-merge` CI on the ubuntu-latest runner w/ `FIREBASE_SERVICE_ACCOUNT_TDU`); reconciled + preserved stranded auth fix → main `9a98488`. NOTE: `fix/tdu-functional-role-L3` is a SEPARATE parked permissions change — do NOT ship without review (flagged to operator). cosem-adcda 1-commit gap still open (offered).
- **Device photo upload** — merchant hero from phone (mig 117 `hero-images` public bucket, own-org write RLS; reuses proof-upload infra; flows to marketplace card).
- **Polish batch** — back-nav `useIsomorphicLayoutEffect` (SSR-safe) + multi-org `?org` honouring (foreign→own fallback verified) — merged `3cb73aa`, deploying.
- **Migrations applied both silos** via guarded `apply-migration-psycopg.py --expect-ref` (NEVER `supabase db push`): 114 (marketplace opt_in+cuisine) · 115 (organization_location PII, cai PII-review CAI-RESP-498) · 116 (REVOKE anon grants — cai's grant-level catch CAI-RESP-499) · 117 (hero-images bucket).

## IASKUSTAZ partnership (Bayan supply of scholars)
- **I Ask Ustaz** (t.me/iaskustaz, Singapore Asatizah Q&A, admin **Nabil Deen**, 2573 members, numbered fatwa DB e.g. #947). Resolves the F-3 "scholar_of_record" gap (their Asatizah = the human scholars).
- Pitch (AI-voiced) sent → **Nabil greenlit** ("show me what you have"). Demo delivered as `/tmp/bayan-demo.html` (encoding + overflow both fixed — send the FILE, not the claude.ai artifact link which is session-gated). **Operator already sent Nabil to the bot** (hence the dead-bot embarrassment) — for now the DEMO is what represents Bayan; hold Nabil-on-the-bot until it truly answers well.
- Next: propose a PILOT (a slice of IASKUSTAZ's answered Q&A → ground Bayan on their vetted answers).

## QUEUED / STAGED
- **cc-infra anon-grant sweep** (bus 10504/10514): 8-9 PII tables on each silo carry default anon table-grants (persons, hr_employees, profiles, pos_orders, inv_customers, outlets, pos_suppliers, qbn_bookings, +wc_order_ingest). Verified NO active leak (RLS denies, `SET ROLE anon`→0 rows) — non-emergency, careful per-table REVOKE-where-safe + CI-lint. Deliberate, not rushed.
- **Storefront backlog**: delivery-driver address release (check the delivery-provider seam), real ratings (needs order-completion loop).
- **Hadi nudge**: device-photo enables it; drafted a nudge message; **pending operator's answer on the channel** to reach Hadi (Green Kook, a real merchant) — I have no direct line.
- **Fleet hygiene**: ~8 idle lanes (cc-branditqr, cc-cosem-qa, cc-ihsanos-cust/donor/perf/qa/store, shipforge) idling — careful drain-and-stop pass (check unpushed work per host FIRST; I'd been under-tracking these).

## LESSONS SAVED (memory/) this session
- **test-end-to-end-before-declaring-live** — the big one; verify real input→real output before "live"/handing to a client.
- pii-table-verify-grants-not-just-rls · lane-branches-strand-unpushed-check-all-hosts · publish-share-runs-from-mini · always-build-for-the-future.

## KEY REFS
- ihsanos main: `3cb73aa` · ai-scholar main: `e03c39f`.
- cai closed CAI-RESP-497 (marketplace design) + 498/499 (location PII + anon-revoke).
- Publishing to share.wingmen.dev = from the **Mini** (scp + `publish_share.sh`), not the hub.
- Screenshots: the hub's playwright can't render (delegate captures to lanes).
- Operator's Telegram: name shows "haikus", id = MUSA_TELEGRAM_ID (286619815).
