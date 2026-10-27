# Business ideas and partnerships, grounded in what Wingmen already runs

*Fable 5.1 temporary lane (cc-substrate-4), 2026-10-27, for Musa op#34970 (run authorised op#34969-34972). Written for the second brain: plain language, no client personal data, company and institution names only. Every claim traces to a repo doc, a report or a memory entry; where the record is silent or contradicts itself, it says so. Prices marked "proposal" are this lane's suggestion, not a recorded fact.*

---

## 0. The one-paragraph version

Wingmen is an agency that has built six products while doing client work: a training-academy platform (assessment, attendance, exams, scheduling, built for ADCDA through COSEM), an HR suite for financial institutions (synthetic demo built for a bank pitch), a Muslim-institution back office (irsyad: donations, collection tins, school fees, live since August with about 1,150 students), a commerce engine with a Telegram surface (storefront, one paying merchant), a bespoke-website pipeline (ex-shipforge, now rebuilding on wingmen.dev), and the fleet itself (a governed multi-agent dev shop that costs about S$440 a month to run). Recorded revenue so far: one S$99 + S$49/month merchant deal, not yet collected, and one unsolicited US$8k payment from Gazzabyte for irsyad. Do first: (1) the wingmen.dev done-for-you website pipeline, because the decision is made, build #1 is in motion and the unit cost is measured; (2) the Muslim-institution back office sold through Gazzabyte, because the client is live, the reseller exists and the pre-Ramadan buying window opens January 2027; (3) the training-academy suite, because it is the biggest asset and ADCDA's parent body is becoming a federal authority, but it needs a named second academy and a residency fix before it earns a build slot.

---

## 1. What already exists (the asset register)

| Asset | What it actually is today | Maturity | Where the record is |
|---|---|---|---|
| Training-academy platform: cosem-adcda (Firebase PWA) + cosem-platform (Next.js, Supabase SG) | Trainee onboarding with ID capture, skill-sheet assessment (units, critical steps, three-attempt roll-up), attendance with geofence and face check, theory-exam answer-sheet scanning, inventory, ORBAT and namelist reports, schedule editor with live clash checks, week-by-week published boards, Arabic-first. About 40 trainers and 238 trainees onboarding from mid-October | Live for ADCDA; cosem-platform is the multi-tenant replacement with onboarding, people, exams, attendance, assets and HR modules already in it | `REPOS.json`, `docs/specs/2026-07-05-adcda-course-admin-engine.md`, memory `project_adcda_platform_convergence`, `project_adcda_schedule_editor_over_engine_solve` |
| AI course administrator (ray-ca) | Scheduler where the model proposes and a constraint solver proves the timetable is conflict-free, then replans the domino when an instructor, vehicle or exam slot moves. Spec validated with COSEM's course administrator | Working prototype on synthetic data; v2 waits on rule values; a sold-or-internal question is open | `reports/cc-caai-charter-20260725.md`, `reports/head-of-revenue-spec.md` |
| cosem-tdu | Joiner onboarding, booking, in/out attendance scans with photo and geo-tag, regulator approval, HR and payroll reports, for a Singapore training entity, in-country Firestore | Live, directed by the entity's own staff | `REPOS.json`, memory `project_cosem_tdu_singapore_entity_fazlie_operator` |
| cosem-video-pipeline | Drive clips to cloud storage, auto-tagged, searchable; 97 clips tagged | Works end to end (REPOS.json still says "specced") | repo README |
| irsyad (Madrasah Irsyad Zuhri Al-Islamiah, via Gazzabyte) | Donation management (collection tins by denomination with two-signature sign-off, campaigns, gapless receipts, bank-statement import with fuzzy donor match, CSV export) and student management (fees, GIRO, financial assistance, parent portal, report cards). Own Supabase project in Singapore, hosted in Gazzabyte's org at $0 to us | Live in production since 2026-08-27, about 1,148 active students, 6,087 donations | `docs/irsyad-ground-truth.md`, `docs/data-store-registry.md`, memory `project_irsyad_silo_architecture` |
| HR suite for financial institutions (ihsanos-hr) | HR, leave, claims, fine-grained roles, hash-chained audit, PDPA controls; built for a bank pitch | Milestone A synthetic demo live, Milestone B was building; 740 commits, last July | `docs/substrate-as-product-roadmap.md` §2, repo |
| Storefront + Telegram surface (ihsanos) | Multi-merchant catalogue, variants and add-ons, daily caps and cut-offs, fulfilment calendar, merchant Mini App, one platform bot, WooCommerce import, payment-provider layer (eNETS, HitPay, Xendit). PayNow confirmation is still manual; the OCR is not built | Live on a seeded store; Hadramawt Kitchen is the founding merchant at S$99 + S$49/month, 12-month lock, not yet invoiced; its chat editor is not deployed | memory `project_storefront_platform_vision`, `project_hadramawt_kitchen_shipforge_dogfood`, `reference_shipforge_storefront_pipeline_not_wired` |
| Website pipeline (ex-shipforge, now wingmen.dev) | Clone-first then fluid conversational redesign, Telegram chat-to-edit, measured cost about US$0.45 per generated site and US$0.03 per routed edit; truhint teardown done; Brandit (the operator's own brand, rebuilding as coffeemedia.sg) and Bariq (a Singapore employment agency) are the first sites | MVP pieces exist; shipforge brand dropped on 2026-10-26 (domain not owned); pricing and launch decisions pending with Musa | `docs/strategy/2026-07-05-shipforge-pricing-pressure-test.md`, memory `project_wingmen_automated_site_pipeline_vision` |
| branditqr | Dynamic-QR app, redirect and scan logging now server-side | Live; admin page still has no login | memory `project_branditqr_adoption` |
| Invoice and receivables tracker | PDF upload, OCR, confirm, aging dashboard, shareable snapshot; tenant #1 is COSEM's Singapore receivables; Musa asked for it to be generalised | About 75 to 80 percent built | memory `project_alderei_invoice_tracker_pwa`, `reports/head-of-revenue-spec.md` |
| fastrans | Freight client backend with a multi-tenant SaaS foundation | Live client project; named first "ihsanify" target | `REPOS.json`, memory `project_substrate_is_the_product` |
| hifz-companion, ai-scholar bots, tarbiyah, dawah-pipeline | Quran memorisation app (live), two Islamic Q&A bots (live, corpus licensed non-commercial), a family Quran programme, an empty da'wah repo | Active or specced; no commercial intent recorded; protected by the 20 percent "tithe" allocation | `REPOS.json`, memory `project_tarbiyah_quran_boys`, `reference_bayan_corpus_openiti_cc_by_nc_sa` |
| The fleet | Hub, console, governance node, quality reviewer, fleet SRE, builder lanes on several Claude Max weekly pools plus a GLM plan; bus, watchdogs, asks ledger, ihsan gate, deploy gates, residency doctrine, design pipeline, fleet console, share.wingmen.dev. Burn about S$397/month, S$440 after the VPS | Runs daily; autoscaler exists but was audited "built but inert"; target is brains on Claude Max and builders on cheap models once substrate 2.0 is operational | `docs/strategy/2026-07-05-financial-architecture.md`, `reports/fable-audit-substrate-20261006/SYNTHESIS.md`, memory `project_target_arch_brains_max_builders_deepseek` |

Three standing facts shape every idea. The monetisation floor is small: 13 managed-site subscriptions cover the whole burn. Weekly Claude Max capacity is use-it-or-lose-it, so speculative build for a warm lead is close to free. And client-data lanes must stay on Anthropic models, so the cheap-builder saving applies to internal and demo work, not to client silos.

---

## 2. Ten ideas

Each: who pays · why now · the asset we already own · the first two-week test · rough revenue model.

### Idea 1. Training-academy suite (assessment, exams, attendance, scheduling), sold to academies beyond ADCDA

- **Who pays.** Training academies and certification bodies whose courses are practical and assessed: civil defence, security, trades, healthcare simulation, aviation ground school, driving and maritime schools. Buyer: head of training or standards officer.
- **Why now.** The platform is live with a government academy and is mid-rollout to 40 trainers and 238 trainees. ADCDA's parent is moving from the Ministry of Interior to a federal civil-defence authority, which means more academies under one buyer. The H2 strategy ranked this the highest value per effort and set a falsifier: a named warm path to academy #3 by 30 September. The record shows none, so this idea needs a buyer conversation, not code.
- **Asset owned.** Skill sheets with critical steps and attempt roll-up, theory-exam sheet scanning, attendance with geofence and face check, schedule editor with live clash checks and week-by-week publishing, the solver-backed scheduler prototype, Arabic-first UI, a synthetic demo tenant ("Meridian Training Academy") already in cosem-platform.
- **Two-week test.** Polish the Meridian demo tenant into a 20-minute walkthrough (enrol a cohort, run a skill-sheet assessment, publish week 1, break the schedule and watch it re-check). Show it to two academies the operator reaches through COSEM and the Singapore civil-defence training world. Success: one dated evaluation or paid pilot.
- **Revenue model (proposal).** Annual licence per academy S$8k to S$30k by cohort size, plus S$5 to S$15 per trainee per course, plus curriculum loading as a one-off. The scheduler is the premium tier at S$500 to S$2,000 a month per campus. Caveat: the record has no COSEM or ADCDA price, and a conflict-of-interest note (COSEM employment versus vendor role) must be formalised before this is sold outside COSEM.

### Idea 2. HR suite for regulated firms (the bank-pitch build, finished and sold)

- **Who pays.** Small and mid-size financial institutions, fund managers, insurers and licensed firms in Singapore that need leave, claims, role-based access and a tamper-evident audit trail they can show an auditor. Buyer: HR head or compliance officer.
- **Why now.** Milestone A is a working synthetic demo and Milestone B was already building when the fleet's attention moved. The hash-chained audit and PDPA controls are the same floor irsyad now runs in production, so the hardest pieces are proven. The fintech-product partner conversation (a former bank payments product lead) gives it a credible first door.
- **Asset owned.** ihsanos-hr (740 commits), the HR module inside cosem-platform, hash-chained audit, the residency pattern (one Supabase project per client in Singapore).
- **Two-week test.** Finish Milestone B to the point of a 15-minute demo, publish it on share.wingmen.dev behind a password, and put it in front of two firms via the fintech partner and Gazzabyte's SME book. Success: one firm agrees to a paid pilot with their real org chart on a dedicated silo.
- **Revenue model (proposal).** S$8 to S$15 per employee per month with a S$300 floor, plus a one-off setup. A 60-person firm is about S$7k a year.

### Idea 3. Muslim-institution back office, sold through Gazzabyte (irsyad productised)

- **Who pays.** Mosques, madrasahs, Islamic classes and charities in Singapore first, Malaysia second: donations and collection tins, receipts, bank reconciliation, school fees, parent portal. Buyer: the administrator or treasurer; Gazzabyte resells and fronts the relationship.
- **Why now.** irsyad is live and the asks it generated this year were audit-driven (bank-statement import, GIRO, denomination grids, outstanding-tin stock), which is what a real category looks like. The H2 strategy fixed January 2027 as the date the suite must be sellable to catch the pre-Ramadan buying season (Ramadan about 8 February to 10 March 2027). A second mosque client existed on the shared database and was decommissioned; the next one should start on its own silo from day one.
- **Asset owned.** The whole irsyad codebase (one repo, per-client silo), bank-statement import with fuzzy donor match, gapless receipts, campaigns, the school module (report cards, teacher and parent portals), HitPay-first payment adapter, hash-chained audit, minors-protection floors, PDPA residency that mainstream SaaS does not offer.
- **Two-week test.** Commercial, not technical. Agree a written reseller sheet with Gazzabyte (see Partnerships), list the ten institutions they already serve or know, and run two demos on the synthetic QA silo. Success: one signed pilot before December so it is live for Ramadan.
- **Revenue model.** The record has two versions: a handoff doc records a split of about S$28k one-time to Gazzabyte and about S$6k a year recurring to Wingmen at irsyad scale, with S$2k to 4k deploy fee per future client and "new module = new quote"; a later memory says that split was never formalised and that Gazzabyte paid US$8k unsolicited on 2026-09-05, now the "reference price for the next madrasah". Proposal: a small-institution tier at S$150 to S$300 a month so a mosque without a S$8k budget can still buy, with Gazzabyte on a fixed share of recurring.

### Idea 4. Done-for-you bespoke websites on wingmen.dev (the truhint model, fully automated)

- **Who pays.** Small businesses in Singapore and the UAE that want a good website without an agency: employment agencies, cafes, print and media shops, clinics. Brandit (the operator's own brand, rebuilding as coffeemedia.sg) and Bariq are the first sites.
- **Why now.** Musa chose this on 26 October: intake, unique bespoke preview in minutes, approve, auto build and deploy, edits by Telegram. The pricing floor is measured (about US$0.45 to generate a site, US$0.03 per routed chat edit, 86 to 93 percent margin at US$29 a month). The reference competitor charges US$299 to US$1,999 one-time plus US$29 to US$249 a month with edit caps and a two-day email turnaround.
- **Asset owned.** Clone-first plus fluid redesign pipeline, WooCommerce import, Telegram chat-to-edit, the design pipeline and quality reviewer, Vercel and Supabase deploy automation, share.wingmen.dev for previews, the per-client Telegram channel pattern (Bariq is its first test).
- **Two-week test.** Ship Brandit and Bariq end to end through the pipeline, then send twenty free previews to a warm list (operator network, Gazzabyte's SME clients, the chef network). The only number that matters is preview-to-paid conversion; the pressure test says anything above about 5 percent works. Hold: the first preview of each site needs a strong-model or human design check before the client sees it, because cheap builder models cannot judge design.
- **Revenue model.** One-time build S$399 to S$1,499 plus care at S$39 to S$99 a month with unlimited conversational edits and SEO bundled (proposal, anchored on the locked US$29/49/79 floor and the truhint benchmark). Thirteen care subscriptions cover the fleet's entire burn.

### Idea 5. Managed commerce for halal F&B and home-based sellers (storefront plus Telegram)

- **Who pays.** Home-based food sellers, small halal restaurants and caterers in Singapore, where capacity-capped pre-orders and PayNow screenshots are the daily reality. Hadramawt Kitchen is the founding customer.
- **Why now.** The platform is live, the first merchant has agreed a price, and WooCommerce import works. The storefront is also the commerce engine for Idea 4, so every site with ordering becomes a merchant. Two gaps block the first invoice: the merchant chat editor is not deployed and PayNow confirmation is manual.
- **Asset owned.** Multi-merchant catalogue, variants and add-ons, daily caps and cut-offs, fulfilment calendar, merchant Mini App, payment-provider layer (HitPay, eNETS, Xendit), branditqr for table and packaging QR, the demo store for onboarding, the specced plan tiers (Free, US$49, US$149, US$399 with a transaction fee).
- **Two-week test.** Close the two gaps for Hadramawt Kitchen, collect the first S$99, then onboard five merchants by Telegram alone from the chef and F&B network. Success: three take live orders and one more pays the launch fee.
- **Revenue model.** S$99 launch plus S$49 to S$79 a month per merchant (the founding price), later 1 to 2 percent on reconciled order value once a payment aggregator is wired.

### Idea 6. Multi-outlet F&B operating stack (the SushiTei play, repeated across Gazzabyte's restaurant book)

- **Who pays.** Restaurant chains with five to thirty outlets on tired incumbents. SushiTei (14 outlets, a Gazzabyte client) is the first target; its breach in July was the buying trigger.
- **Why now.** The recon is done: the incumbent scores 55 on mobile performance with a 32-second first paint; irsyad scores 91 at 1.3 seconds on the same test. Security-as-architecture (per-outlet isolation, hash-chained audit, no plugin soup) is a pitch that lands after a breach, and a five-phase roadmap is already published.
- **Asset owned.** About 60 percent exists: brand site (Idea 4), ordering (Idea 5), QR (branditqr), the modular-monolith substrate. Net new: kitchen display, outlet hierarchy, inventory, reservations, loyalty.
- **Two-week test.** Build the Phase 0 beachhead for one outlet on a demo tenant (brand site, ordering, table QR) and a side-by-side cold-load video against the incumbent. Present it with Gazzabyte. Success: a scoped Phase 1 order.
- **Revenue model (proposal).** Per-outlet subscription S$150 to S$300 a month plus implementation; a 14-outlet chain is S$25k to S$50k a year recurring, with Gazzabyte on the reseller share.

### Idea 7. Invoice and receivables tracker for small firms billing slow payers (the COSEM module)

- **Who pays.** Small service firms that bill government or large corporates and wait: training providers, consultancies, contractors. COSEM's Singapore receivables are tenant #1.
- **Why now.** It is 75 to 80 percent built, Musa asked for it to be generalised, and the data owner and residency are settled (COSEM, Singapore). The L1 scope (upload PDF, OCR, confirm, aging dashboard, shareable snapshot) can be finished in two weeks.
- **Asset owned.** The three-layer design (track, bill, pay), the verify-before-trust OCR rule, irsyad's bank-statement import, the audit chain, the specced billing and plan-limits engine.
- **Two-week test.** Ship L1 for COSEM with real invoices on the Singapore stack and replace its monday.com tracking, then show it to two other small training or consulting firms. Success: COSEM uses it weekly and one other firm asks for access.
- **Revenue model (proposal).** S$29 to S$79 a month per firm; L2 invoicing and L3 collection later at a higher tier.

### Idea 8. A governed AI engineering team as a service (the fleet, sold by the outcome)

- **Who pays.** Founders, agencies and SMEs that need software built, audited or fixed and cannot hire. Two entry products: a fixed-price "audit and harden" package (security, performance, mobile design, 48-hour turnaround, the branditqr and SushiTei recon shape) and a monthly retainer for a build queue with a Telegram front door and a published weekly report.
- **Why now.** The fleet already pays for weekly token pools that reset; idle capacity is wasted. The governance that makes it safe (independent review on every money or security change, residency doctrine, ihsan gate, deploy gates) is unusually mature and is itself the pitch: an AI dev shop that structurally cannot ship unreviewed code. The strategy doc parked "AI agency in a box" as licensing; the service version needs no licensing and uses what runs today.
- **Asset owned.** The substrate, the ihsan-gate manifest, the design pipeline, the quality reviewer, the per-client Telegram group pattern already used with external experts, the asks ledger, share.wingmen.dev.
- **Two-week test.** Sell two 48-hour audit packages to warm contacts (a Gazzabyte client, an SME in the operator's network). Deliver through the normal pipeline with a published report. Success: both pay and one converts to a retainer.
- **Revenue model (proposal).** Audit package S$1,500 to S$5,000; retainer S$3,000 to S$8,000 a month for a defined weekly capacity. Client-data work stays on Anthropic models, so price it on that cost, not on the cheap-builder cost.

### Idea 9. Gulf government training and assessment pipeline (the ADCDA reference, deliberately slow)

- **Who pays.** The federal civil-defence authority ADCDA is moving under, other Ministry-of-Interior academies, and UAE government training bodies. Large tickets, long cycles, sovereign hosting required.
- **Why now.** The ADCDA rollout is a live reference inside a Gulf government, and a second UAE government assessment request is already recorded, blocked only on a UAE-resident data silo. The strategy doc is explicit: sell portability and jurisdiction-of-choice honestly, migrate hosting only when a contract funds it, and expect sovereign hosting (Core42-class) as a condition. Today real government ID data sits on a US cloud; the Firestore-to-Postgres convergence is the fix and is already the plan.
- **Asset owned.** Idea 1, the ADCDA reference, Arabic-first UI, the Singapore neutral-contracting posture, the residency doctrine, the July hosting research (Core42, deploy into the client's own tenancy, on-premise Mac Studio option).
- **Two-week test.** Not a build. One structured conversation with the ADCDA contact about which neighbouring academy or the new federal body has the same problem, and one call with a Core42 or Azure-UAE partner about deploying into a client-owned tenancy. Success: a named second Gulf conversation and a hosting answer.
- **Revenue model (proposal).** Per-academy annual licence in the AED 150k to 500k band plus implementation; hosting billed through the client's tenancy with a managed-infra margin.

### Idea 10. Quran and tarbiyah learning suite for madrasahs and families (hifz-companion, tarbiyah, the content pipelines)

- **Who pays.** Madrasahs and Quran classes as an add-on to Idea 3, and parents directly. The operator's own boys' Quran programme is the natural first classroom.
- **Why now.** The pieces exist and sit in the protected 20 percent allocation, so they get built anyway; the question is only whether they are packaged. Selling through Idea 3's channel costs nothing extra. The video pipeline already tags and searches clips, which is most of a lesson-to-short-clip tool for institutes.
- **Asset owned.** hifz-companion (live, spaced-repetition engine, about 210 tests), the tarbiyah programme content, the video-tagging pipeline, the Q&A bots (note: their corpus licence is non-commercial, so they stay free).
- **Two-week test.** Run one madrasah class through hifz-companion with a teacher view, and send one automated weekly progress message to parents by Telegram. Success: the teacher asks to keep it after the pilot.
- **Revenue model (proposal).** Free for families; S$2 to S$5 per student per month for institutions, bundled into Idea 3; content-pipeline output to mosques and institutes at a flat monthly fee.

**Parked on purpose.** A Shariah-compliant payments or remittance corridor between the UAE and South-East Asia (real vision, regulator-first, owned by the fintech-partner conversation, not a two-week test). "Neutral Singapore data custody" as a stand-alone premium (unclaimable until the ADCDA stack stops contradicting it). Licensing the substrate itself (needs a second operator who can run it; Idea 8 earns the evidence first). A freight SaaS from fastrans (real foundation, no second freight buyer in the record).

---

## 3. Five partnership targets, by type

1. **Reseller channel: Gazzabyte (existing, formalise it).** They bring irsyad, SushiTei and a book of Singapore SMEs and institutions, they host the irsyad silo in their own Supabase org, they front the client relationship and they have already paid. What is missing is a written sheet: share of one-time and recurring, who invoices, who supports first-line, what happens to the silo if either side walks. The record holds two conflicting versions of the split; one page settles it and unlocks Ideas 3, 5, 6 and 8. Musa's stated intent to move fleet compute onto their office server makes the paper more urgent, not less.
2. **Domain and regulatory partner for money and HR: the former bank payments product lead (the Hafiz brief).** Fills the gap the fleet has: regulated-product judgment, bank and PayNow relationships, and a human who can carry accountability with a regulator. Scope him to the money modules (storefront payments, tabung collections, Idea 7's collection layer) and to the HR-suite door (Idea 2), under NDA, before any corridor talk.
3. **Build and sales partner for the website and commerce line: Desmond Shen (existing, scoped).** He already holds dispatch and deploy authority for shipforge and storefront. Idea 4 needs someone to run the twenty-preview test and the client calls while the operator is on Gulf and academy work.
4. **Sovereign-hosting partner for Gulf deals: a Core42 or Azure-UAE delivery partner.** Idea 9 cannot be sold without an answer to "where does our data live", and buying a sovereign tenancy for one small tenant is bad economics. A partner who deploys Wingmen software into the client's own tenancy turns hosting from a blocker into their line item.
5. **Public funding and institutional distribution in Singapore: IMDA pre-approved-vendor status, plus the operator's own civil-defence training and mosque networks.** The head-of-revenue spec already names IMDA SMEs-Go-Digital and Tech Data; pre-approved status means an SME pays a fraction of the Idea 4 and 5 price out of pocket. The civil-defence training network is the only named warm path to academy #3 for Idea 1, and the mosque and scholarly network is the distribution for Ideas 3 and 10.

Two more worth one email each: a Singapore payments aggregator (HitPay is already first in the adapter) partner programme so merchants get PayNow, cards and payouts without Wingmen touching funds; and COSEM itself as a formal channel partner for Idea 1 once the employment-versus-vendor disclosure is written down.

---

## 4. The three to do first, and why

**First: Idea 4, the wingmen.dev website pipeline.** It is the shortest path to recurring money the fleet has ever had. The decision is made, build #1 is dispatched, the unit cost is measured rather than guessed, and the only unknown is preview-to-paid conversion, which a two-week test answers. It feeds Idea 5 (every site with ordering becomes a merchant) and gives Idea 8 its showcase. Risk to hold: the bespoke-quality check on the first preview cannot run on cheap builder models.

**Second: Idea 3, the Muslim-institution back office through Gazzabyte.** It is the only idea with a live production client, a reseller who has already paid, and a calendar: sellable by January 2027 to catch the pre-Ramadan buying season. The audit-driven asks show the category is real. The first move is commercial, not technical: the reseller sheet, then two demos. Execution on irsyad stays with the coordinator lane and the Gazzabyte partners' sign-off per the governance floors; this console body does not drive it.

**Third: Idea 1, the training-academy suite.** It is the largest asset and the only one with a government reference, which is why the strategy ranked it first on value. It is third in order because the 30 September falsifier was missed (no named academy #3) and because real government ID data still sits on a US cloud, which blocks both the second UAE request and any honest residency pitch. Two weeks of conversations (the Singapore civil-defence training network, the COSEM contact, the federal-authority transition) plus the Firestore-to-Postgres convergence decide whether it gets a Q1 2027 build slot. If a named academy appears, it jumps to first.

Ideas 5, 6 and 7 ride behind these on the same engines and should be taken when a buyer appears rather than scheduled. Idea 2 is the sleeper: most of it is built, and the fintech partner is its door. Idea 8 should be sold opportunistically whenever the weekly token pool is under-used, because its marginal cost is close to zero.

---

## 4a. Direct ask (op#34977): the quickest way to 100 USD a day with the substrate

100 USD a day is about 3,000 USD or 4,000 SGD a month. Today the fleet has one agreed price (Hadramawt Kitchen, S$99 + S$49 a month, not yet collected) and one payment (Gazzabyte, US$8k, unsolicited). Nothing recurring covers burn. The fastest money is selling the fleet's build hours to warm contacts; recurring software at 100 a day is two to three months away at best. The constraint is Musa's selling hours, not the fleet's build hours.

| Route | Days to first dollar | What we sell, to whom | Reuses | Path to 100 USD a day | Main risk |
|---|---|---|---|---|---|
| 1. Fixed-price build and audit work | 3 to 7 | 48-hour security, performance and mobile-design audit with fixes at S$1,500 to 3,000; bespoke site build at S$500 to 1,500. To Gazzabyte's SME book, Bariq, Hadramawt Kitchen's circle, COSEM's circle, Musa's network | Whole fleet, quality reviewer, design pipeline, share.wingmen.dev, cc-finance PayNow invoice, per-client Telegram channel | One audit every 10 days is about S$4,500 a month; or two site builds a week at S$700 is about S$5,600 a month | Services stop when Musa stops pitching; delivered design still needs a strong-model or human eyeball |
| 2. Collect and quote what already exists with Gazzabyte and COSEM | 7 to 14 | The parked irsyad modules the client already asked for (Tabung Fajr tin inventory, GIRO and bank-statement upload, bulk receipts, zakat distribution reports) at S$3,000 to 6,000 each under the recorded "new module = new quote" rule; the COSEM invoice tracker as a paid module; the Hadramawt Kitchen S$99 | irsyad in production, cc-irsyad-coord and autoscaler lanes, hash-chained audit floor, receivables tracker | One S$4,000 module a month is 100 USD a day on its own | Money and audit modules were parked by Musa in July and need design-first plus Gazzabyte partners' sign-off; execution stays with coord on the irsyad key; the Gazzabyte split is unformalised, so the reseller sheet comes first |
| 3. wingmen.dev website pipeline | 10 to 21, not quick to 100 a day | One-time build S$399 to 1,499 plus care S$39 to 99 a month to SMEs in Singapore and the UAE | Clone-first redesign pipeline, Telegram chat-to-edit, Vercel and Supabase deploy, measured US$0.45 per site and US$0.03 per edit | Build fees bridge (two a week at S$700 is 100 a day); recurring alone needs 35 to 100 care subscribers at US$29 to 99, which is months at an unknown conversion rate | Build #1 not shipped end to end; pricing and launch decisions pending; the uniqueness eyeball is the hardest step to automate |

Not quick, do not count on them for 100 a day: storefront subscriptions (S$49 a month needs about 80 merchants; PayNow confirm is manual and the merchant editor is not deployed), the training-academy suite and Gulf govtech (no named second academy, residency blocker), the HR suite (Milestone B unfinished), licensing the substrate, hifz or the bots.

Recommendation: run Route 1 this week with two named prospects, run Route 2 in parallel through coord with the reseller sheet first, and let Route 3 land behind them. Posted to orch-console as bus #78638 at 19:2xZ.

---

## 4b. Direct ask (op#34979): Zahidah, Mama Da's Broth, and what the fleet can do

Public information only; nobody contacted, nothing logged into. Family member of the operator; no personal details recorded here beyond the public business.

**What is public today.** Instagram @mamadasbroth.sg: "Mama Da's Broth Singapore", bio "Collagen Packed, Nutritious, Great for Your Gut, 100% Natural Ingredients, 100% Muslim Owned, Singapore's Very Own", 169 followers, 12 visible posts dated April to June 2025 and nothing since. The Linktree goes to a Cococart shop, WhatsApp, YouTube, TikTok and a corporate-enquiries email. The Cococart shop says "closed for now but we'll be back soon" with no product list; only "10 percent off first order above S$60" and "free shipping above S$100" remain. So the brand exists and the positioning is good (halal-adjacent collagen bone broth, Muslim-owned, made in Singapore), but it is paused; every move below is a relaunch, not growth. Market anchor: the closest Singapore comparable (BabyBroth) sells 200 ml jars at S$15 to 17.50 and 8 x 50 ml baby tubs at S$21 to 32, S$13 flat delivery, free above S$100, no halal claim, no collagen claim, no chef credential. That gap is hers. One check before anything goes on packaging: a public search did not surface a MasterChef Singapore contestant page under her name, so confirm the exact season and credited name so the claim is citable.

**(a) Top three growth moves for the broth business**

1. **Relaunch as a weekly pre-order with capacity caps**, not an always-open shop. Effort low. Revenue speculative: at S$15 to 18 per 500 ml pack and 40 to 80 packs a week, roughly S$2,500 to 5,500 a month before costs. First step this week: two cook days, a two-day order window on the storefront, and one honest Instagram post about the return.
2. **Own the "halal collagen broth for mums and babies" lane.** The comparable owns "baby" but cannot say halal; she can say halal, Muslim-owned, MasterChef-trained and food-science-informed. Add a no-salt baby variant and a postnatal confinement pack. Effort medium. Revenue speculative: confinement packs at S$120 to 180 for two weeks, 10 to 20 mothers a month is S$1,500 to 3,500. First step: one post and one reel on the baby and postnatal use case; ask three mothers in her network to pre-order a trial pack.
3. **B2B and recurring**: supply halal cafes, hotpot and soup stalls and gyms with broth as a base or menu item, plus a monthly family subscription. The Linktree already has a corporate-enquiries line. Effort medium to high (volume, cold chain, possibly a licensed kitchen rather than home-based). Revenue: one cafe at 20 litres a week at S$8 to 10 a litre is about S$700 to 900 a month per account; three accounts plus 30 subscribers at S$60 a month is roughly S$4,000 a month. First step: a one-page wholesale sheet and two warm asks through the F&B network Hadramawt Kitchen sits in.

**(b) Top two new-venture options on the MasterChef credibility**

1. **Premium halal private dining and corporate chef's table**, 8 to 12 seats, two sittings a month, plus brand collaborations. Effort medium, highest margin per hour of her time. Revenue: S$150 to 250 a head, 10 seats, two sittings is S$3,000 to 5,000 a month; corporate dinners pay more. First step: one menu, one date, one post, first sitting sold to friends-of-friends at a founder price.
2. **A paid cooking programme**: a short recorded "weeknight halal cooking" or "broth-based cooking for families" course plus live monthly classes, with recipes as the lead magnet (the Linktree already lists two recipes). Effort medium once, then low. Revenue speculative: S$49 to 99 a course, 30 to 100 buyers a quarter is S$1,500 to 10,000 a quarter, plus live classes at S$60 to 90 a seat. First step: record one 20-minute lesson on a phone and sell it for S$19 to test demand before building anything.

Honest note: (b) trades on her time and presence; (a) trades on product and can run while she studies. Pick by the hours she actually has.

**(c) Where the substrate can do real work for her**

1. **Ordering and operations.** Onboard Mama Da's Broth as a merchant on the storefront (Telegram ordering, variants, daily caps, fulfilment calendar, cut-offs) so pre-orders, capacity and delivery slots run without WhatsApp back-and-forth. This is exactly the home-based F&B seller the platform was built for; she would be merchant number two after Hadramawt Kitchen and a real test of Telegram-only onboarding. Effort for us low. First step: a seeded demo store with her products this week; she decides whether the Linktree "Buy" button points at it.
2. **Content.** A weekly content kit from the fleet (three captions, one reel script, one recipe card in her voice) drafted for her approval, plus a landing page on wingmen.dev through the website pipeline to replace the closed Cococart page. The per-client Telegram bot pattern exists already, so a terse assistant channel is cheap. Effort low. First step: she approves a voice sample; the fleet drafts the relaunch post and four weeks of content.
3. **Numbers and compliance.** A one-page unit-cost sheet (bones, gas, packaging, delivery, labour) and a short note on Singapore home-based food business rules and what a licensed-kitchen step would cost, so (a)3 and (b)1 are priced honestly. Effort low. First step: she lists ingredient costs per batch; the fleet turns it into prices and break-even. Her business data gets its own store, not a shared project, per the residency rule.

Not for the fleet: the cooking, the brand-voice decisions, and anything that needs her face on camera. Posted to orch-console as bus #78648.

---

## 5. Honest caveats

- Every price marked "proposal" is this lane's estimate. The recorded prices are: Hadramawt Kitchen S$99 + S$49/month (not yet collected); the locked US$29 hook, US$29 site, US$49 to 79 store floor; the Gazzabyte split (two conflicting records); the US$8k irsyad payment; the specced ihsanOS tiers. Nothing else is a recorded price.
- No COSEM, ADCDA or TDU commercial terms exist in the record. The head-of-revenue spec itself asks whether the AI course administrator is sold or internal.
- Allocation doctrine still applies: the 20 percent tithe to ihsanos, scholar and hifz is off the top; Ideas 4 and 5 sit in the near-cash share; Ideas 1 and 9 in the cosem/govtech share.
- Every idea that touches client rows inherits the residency rule: the client's store is designated before the first write, and bare product names never stand in for a data store.
- Three records disagree with REPOS.json or with each other and should be reconciled in the second brain: cosem-video-pipeline is working, not specced; the shipforge strategy says the domain was owned, the October memory says it was not; the Gazzabyte split is "operator-ratified" in one doc and "never formalised" in another.

---

## NOVEL IDEAS (op#34981-82): five moves from this lane's vantage point

Written after a full read of the portfolio, the strategy docs and the money record. Each has one line on why it is feasible for Wingmen specifically, the first concrete step, and what it unlocks.

### N1. The speculative-build engine: turn perishable token capacity into a weekly free prototype for a warm prospect

- **The idea.** Every week the fleet builds one working prototype (a deployed demo tenant with synthetic data, not a mock) for the warmest unconverted lead on the revenue ledger, publishes it on share.wingmen.dev with a price attached, and the operator sends one message: "we built this for you, here is what it costs to make it yours". A public "built this week" feed becomes the marketing.
- **Why feasible for us.** The Claude Max weekly pools reset and unused capacity is lost; the design pipeline, quality reviewer, demo tenants (Meridian Training Academy, the storefront demo store, the HR demo) and share.wingmen.dev already exist. No human agency can afford to build before being paid; this fleet already pays for the capacity whether it builds or not.
- **First step.** A small `speculative_builds` queue fed from cc-finance's opportunity list, triggered when the fleet SRE's pool-usage signal shows under 60 percent used by day four of the window. First target: the SushiTei Phase 0 outlet demo.
- **Unlocks.** Sales without a salesperson, a proof-of-work pipeline instead of proposals, and a weekly public artefact that compounds into reputation.

### N2. Sell the governance, not the code: a hosted assurance gate for other teams' AI agents

- **The idea.** Package what makes this fleet safe (the ihsan gate's six checks, independent review on money and security changes, fail-closed hooks, hash-chained audit, residency checks) as a GitHub App plus a review lane that other small teams' agent fleets must pass before merge or deploy. "Your agents write the code; ours refuse to let it ship unreviewed."
- **Why feasible for us.** The ihsan-gate manifest is already machine-readable, the pre-push and PreToolUse hooks exist and are tested in anger, and cc-quality already is the independent reviewer. The buyer category (teams adopting coding agents) is the fastest-growing one in software and carries no client-data residency burden for Wingmen, because the gate reads diffs, not customer rows.
- **First step.** Install the pre-push hook plus ihsan-gate manifest as a GitHub App on one external repo (a Gazzabyte project) and have cc-quality post its verdict as a required check. Charge per repo per month.
- **Unlocks.** A product built on the one asset no competitor has copied (two years of governance doctrine enforced in code), with margins that do not depend on the operator's hours.

### N3. "Your Supabase, our software": make the Gazzabyte hosting pattern the standard commercial shape

- **The idea.** irsyad already runs in the partner's own Supabase org at zero infrastructure cost to Wingmen. Make that the default for every client: the client owns the silo and pays the cloud bill directly; Wingmen sells software plus operations as a subscription with an export-anytime guarantee written into the contract.
- **Why feasible for us.** One-repo-zero-forks and per-client silos are already enforced doctrine; the direct-apply migration tooling already handles multiple stores; the registry already tracks each store's ref and region. The Gulf and Muslim-institution markets want exactly this and cannot buy it from US SaaS.
- **First step.** A one-page "bring your own Supabase" onboarding runbook (project creation, region, roles, the migration applier pointed at it, the export script) applied to the next madrasah or mosque on day one.
- **Unlocks.** The honest sovereignty pitch at last (the data really is theirs, in their jurisdiction), infrastructure cost that stays flat as clients grow, and the residency gate turned from a sales blocker into the sales argument.

### N4. Provably conflict-free scheduling as one engine across four verticals

- **The idea.** The AI course administrator's "model proposes, solver proves" engine is not an academy feature. Point it at madrasah timetables and teacher allocation (irsyad), restaurant rostering across 14 outlets (SushiTei), mosque programme and venue scheduling (Idea 3), and cook-day capacity for home-based sellers (the storefront's caps and fulfilment calendar are already the data). Brand property: "a timetable that is proven, not guessed".
- **Why feasible for us.** The constraint model, the independent verifier and the SME-validated spec exist; the storefront already stores caps, cut-offs and calendars; irsyad already stores classes and teachers. Nobody else in these markets has a solver, and the LLM-only competitors cannot promise conflict-freeness.
- **First step.** Expose the scheduler as an internal service and call it from the storefront's fulfilment calendar for Hadramawt Kitchen's weekly capacity plan; measure one week of real use.
- **Unlocks.** One engine amortised across every vertical, a premium tier for Ideas 1, 3, 5 and 6, and a defensible technical claim.

### N5. A second-brain-native company: the vault steers the fleet and the fleet writes the vault

- **The idea.** Go beyond the read-only projection now in flight. Every decision, ask, held commitment and revenue event lands as a dated capture in the operator's vault inbox; once a week the fleet reads the vault for new intents the operator wrote in his own words and turns them into asks on the ledger; this report's ideas live there as a living board with an honest scoreboard per idea (tested, paying, dead).
- **Why feasible for us.** The vault audit is done, the capture rules and protected folders are defined, the asks ledger and ingest already exist, and the operator already thinks by writing notes. The missing piece is a two-way loop, not new infrastructure.
- **First step.** A nightly read-only projection of open asks, the revenue ledger and this report's ten ideas into the vault inbox as captures, with a freshness stamp; then one weekly "intents from the vault" pass that proposes asks and never writes to protected folders.
- **Unlocks.** The operator directs the company by writing, the strategy memory survives every agent reset, and the fleet stops depending on whoever remembers what Musa said in July.

---

## Appendix: research briefs (five read-only subagents, 2026-10-27, summarised)

- **COSEM / ADCDA.** Live Firebase PWA with roughly 90 to 157 cloud functions; onboarding, skill sheets, attendance, observations, inventory, reports, phone-OTP and passkey login. cosem-platform (Supabase, Singapore) owns scheduling and is the convergence target. Scheduling direction changed on 2026-10-19 from engine-solve to a drag-and-drop editor with live clash checks. Residency is the main blocker: government ID data on Firestore; on-premise or sovereign hosting needs the Postgres move. No pricing or contract found. Sources: `docs/data-store-registry.md`, memory `project_adcda_platform_convergence`, `reference_adcda_onprem_residency_requires_firestore_to_postgres`, `project_cosem_theory_omr_trial_spec`.
- **irsyad / Gazzabyte.** Live since 2026-08-27 on its own Singapore Supabase project inside Gazzabyte's org; donation and student modules; bank-statement import, gapless receipts, hash-chained audit on money corrections, minors excluded from donor aggregation, "IRAS" and "tax-deductible" wording banned until a Gazzabyte partner lifts it. Payment adapter HitPay-first; eNETS needed for fees. Autoscaler is design-only. Sources: `docs/irsyad-ground-truth.md`, `docs/governance-floors.md`, `docs/irsyad-autoscaler-design-v1.md`, `reports/nazim-handoff-20260802.md`, memory `project_first_revenue_irsyad_8k_20260905`.
- **Commerce and web.** Storefront live on a seeded store; PayNow webhook is a stub; Hadramawt Kitchen editor not deployed and payment parked until editing is real; SushiTei is pitch-only; dookana sunset; shipforge dropped 2026-10-26; wordpress-sites is a Layer-1 clone toolchain; Bariq preview in progress and first test of per-client Telegram channels; brandit.sg rebuilding as coffeemedia.sg in faithful and elevated forks; branditqr admin still has no login; share.wingmen.dev live. Sources: memory `project_storefront_platform_vision`, `project_hk_editor_not_deployed`, `project_sushitei_fb_vertical`, `project_dookana_root_landing_vs_belii`, `reference_shipforge_storefront_repo_topology`.
- **Fleet and smaller products.** Ihsan gate (six checks, machine-readable manifest), governance floors, hash-chained audit, residency doctrine; multiple Claude Max pools plus a GLM plan; DeepSeek allowed for internal lanes only; autoscaler "built but inert" in the October audit; hifz-companion live with about 210 tests; ai-scholar bots live on a non-commercial corpus; fastrans has a multi-tenant SaaS foundation; HR suite Milestone A demo live; dawah-pipeline repo empty. Sources: `docs/ihsan-gate.md`, `docs/self-improvement-loop-spec.md`, `reports/fable-audit-substrate-20261006/SYNTHESIS.md`, memory `feedback_client_data_lanes_never_on_non_anthropic_providers`.
- **Partners.** Gazzabyte (partner and reseller, two directors, paid US$8k unsolicited, split never formalised, Musa intends to consolidate compute at their office); Desmond Shen (scoped shipforge and storefront partner with deploy authority, no equity or revenue terms recorded); the fintech partner (former bank payments product lead, scoping sprint proposed, Shariah-rails brief sent, no formal engagement); COSEM's course administrator as scheduling SME; a donated Claude Max subscription from a supporter; COSEM and ADCDA as client with no recorded terms. Geographies: Singapore (clients, residency), UAE (operator base, ADCDA), Malaysia and Indonesia (only as a payments-corridor aspiration). Sources: memory `reference_shuk_and_wan_are_gazzabyte_our_partner`, `user_desmond_shen_partner`, `project_hafiz_fintech_partner`, `project_shariah_compliant_uae_sea_rails`, `project_ai_course_administrator_ray`.
