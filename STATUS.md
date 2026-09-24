# wingmen-orchestrator STATUS

Last Updated: 2026-07-08 — **NAZIM MOVED TO THE MINI**: operator-reachable via @nazim_cto_bot, round-trip verified both ways. Self-contained nazim-console channel on the always-on Mini (host-scoped `INGEST_CHANNELS` ingest + `nazim_send.sh` own-bot outbound + `operator_log` console-scope for `tag='nazim-console'` + Opus 4.8 session; `nazim-ingest` + `nazim-session` both launchd-KeepAlive → reboot-durable). Also killed the Mini's auto-revived two-fleet split-brain (legacy `wingmen_orch.py` + zombie fleet-control disabled; `lane-watchdog` sealed + guarded vs the 07-03 phantom-inject class). See **## CURRENT (2026-07-08)**. | PRIOR 2026-07-04 — **GOUMLYNE UNBLOCKED (standing irsyad blocker closed)**: `GOUMLYNE_DATABASE_URL` had never been minted anywhere (empty on Studio AND Mini, no MacBook/shell-history trace). Discovered the orch `SUPABASE_ACCESS_TOKEN` has full management access to the goumlyne project (`goumlynecruxrlmzlntp`, Gazzabyte partner org `fhhokzynpxymuarftjte`) — rotated the DB password via the Management API (old one provably consumerless: var empty fleet-wide, app uses service-role keys), minted the session-pooler connection string, verified a live psycopg connect (79 public tables, ACTIVE_HEALTHY), set it in the Studio `.env` and synced to the Mini's (Studio SSH key now authorized on the Mini — durable access). WC-ingest 083 draft→psycopg-apply→write-proof→idempotency handed to the mirror lane; operator confirmed via tg_send. Orch session now on **Fable** per operator (faster operator-facing replies; hard rule: reconcile `operator_log.unprocessed()` at the top of every turn, never passive-idle-hold). | PRIOR 2026-07-02 — **Mac Mini decommissioned; interim cutover to operator's MacBook (Abu Dhabi)**: operator relocated to Abu Dhabi; Mac Mini + Mac Studio remain in Singapore. Mini fully decommissioned as an interim step ahead of the already-planned Linux-cloud migration (`docs/superpowers/plans/2026-07-01-mac-mini-to-linux-cloud-migration.md`); Mac Studio stays up (Zahidah pgvector pipeline unaffected). Pulled all uncommitted/untracked work off the Mini first (migrations 009–013, new nervous_system modules, launchd plists, reports, plan docs — same uncommitted state now on the MacBook). Stood up the phone-reachable control plane only — **cc-orchestrator** (tmux `orch`), **cai** (singleton, tmux `cai`), **tg-bridge** (launchd) — on the MacBook; the full engineer-lane fleet (mirror/scholar/cosem-adcda/cosem-tdu/shipforge/storefront) and fleet-console stay down until proper cloud infra exists. Portability fix landed (`scripts/boot_orch.sh` now resolves `$HOME`/`claude` dynamically instead of hardcoding the Mini's paths). Open item: `--dangerously-skip-permissions` currently shows "Claude API" instead of "Claude Max" billing on both machines (same CLI v2.1.197) — contradicts `boot_cai.sh`'s "Verified 2026-06-17" comment; not yet root-caused, deferred as non-blocking per operator. See **## CURRENT (2026-07-02)** below. | PRIOR 2026-06-19 SGT — **planned power-outage handled**: graceful shutdown 15:45 SGT, clean reboot, **full fleet restored** (6 lanes + cai). Fixed a latent boot-bricker: `agents.repo_scope` had two double-claims (ihsanos: cc-ihsanos+cc-storefront; deprecated cc-cosem overlapping the split cosem identities) that fail-fast `load_family_map()` and bricked every fresh lane boot — only visible after a reboot. Fix (reversible): cc-cosem→[], cc-storefront→['ihsanos-storefront']. Sent cai for review (msg #2646). CTO-bot 'down' = watchdog false-alarm (bot decommissioned). Full state: session_digest #199. See **## CURRENT (2026-06-19)** below. | PRIOR 2026-06-18 SGT — overnight 5-lane fleet run; **cosem-tdu IHSAN delivered live** (form+function); credential consolidation done; #111 auto-wake genuinely live; cc-reviewer design dimension wired. Full state in substrate: session_digest #185 + repo_context(wingmen-orchestrator). See **## CURRENT (2026-06-18)** below. | PRIOR 2026-06-17: fleet autonomy build-out (cc-reviewer live; #111 committed). | PRIOR 2026-06-16 SGT (Irsyad persona-runner **★ G3 GREEN + #2175 CLEARED FOR UAT (cai CAI-RESP-242)** — stood DOWN to monitoring; no residual gate is mine. Re-run triggers ONLY: (a) any RLS/policy migration applied to Irsyad silo OR pooled, (b) one final 23/23 smoke immediately pre-demo. G3 cross-validated green both layers (23/23 route/UI == DB-RLS oracle, zero divergence, #2284, qa #678-687, commit 592601b). Open sale-commit mutation-exec test = CAI-RESP-241, cc-ihsanos scope, non-blocking, NOT mine. Finding 1299 (pos_orders PUBLIC insert) cc-ihsanos-owned, not a G3 item. History: J2–J8 deterministic sweep BUILT (66/66 unit green, commits 2a42e6b/71c6e20) incl. new `access` verdict for server-enforced /people writes; cai #2235 set scope=(A) access/RBAC/PII matrix, mutation-exec→G2/UAT, RIDER: cross-validate my route-guard matrix vs cc-ihsanos DB-RLS oracle (sent #2241). Scoped security audit DELIVERED (work_outputs#212): **A1 P1** pos_orders anon-insert forges paid/cross-tenant orders (WITH CHECK true); A2 Medium org column over-exposure; A3 NRIC PASS — flagged cc-ihsanos #2244. DB-RLS cross-validation now RECONCILED (cc-ihsanos #2243/#2248 → my #2256): C1 parent leak CLOSED post-069; J5/J7 = route-only-denial defense-in-depth note (verdict stands); J8 masked-NRIC-only-for-admin cell OWNED by my live browser sweep (DB can't certify it); A1/A2 concur (qa 1299/1300). Live full sweep now blocks ONLY on cai ruling re login-URL target (escalated #2257: cc-ihsanos says silo, CAI-RESP-231 says pooled — my fail-closed guard refuses the silo) + operator-exported synthetic creds.)
Build Status: green
Deploy: n/a (feature-flagged module, default OFF; no deploy — Mac Mini bot + Mac Studio worker host)

## CURRENT (2026-08-14) — hub reset+reconciled (06:43Z); COORD-TAKEOVER in motion; #250 LANDED both silos; #299 held on tz fix

**Fresh hub up 2026-08-14 ~06:43Z** (reset by Nazim, op#12822). cc-orchestrator @ wingmen-core, tmux `orch`, holds orch-hub lease (renewed). fleet_health_lease = cc-fleet-health@Sheikhs-Mini → watchdog/fleet-status DEFERRED. Inboxes reconciled.

### 2026-08-15 (~09:40Z hub turn)
- **CAI-858 C1 mig176 `write_audit_log_system` — ✅ HUB-APPLIED BOTH SILOS (dormant).** cai §6.6 GRANTED (CAI-916, #21810/#21817). Fetched byte-exact from origin @ `053e14b` (SHA verified 053e14b98452…), applied via direct psycopg (NOT db push), DSN residency-guarded per silo. ceayj + goumlyne: PRE=absent → POST exists; `proacl {postgres=X/postgres,service_role=X/postgres}` = EXECUTE service_role-ONLY (no anon/authenticated/PUBLIC). NULL-actor SECDEF appender w/ CAI-916 D1 provenance + D2 auth.uid belt + CAI-782 shared per-org lock. **Dormant — no caller until the C5 place-order fold (separately gated on cai's independent post-apply proacl verify, requested #22011).** build_log 1005.
- **HK (shipforge) #22002 — NOD to build IG-feed + Google-reviews parity** (op#13124 "true to original"): official lightweight client-side embeds only, graceful static fallback, verify-real-Google-listing-before-building, preview-only/reversible. → #22006.
- **Irsyad client (Shuk) turn-on/%/move-it pings (13239-13244, ~6h waiting)** relayed to cc-irsyad-coord (client-voice owner, alive) w/ consolidated rollup #22005; hub stays off the client thread (BEAT-3). Root cause = gazzabyte-irsyad ingest still routes to hub log; offered coord the SRE re-point. Receipt header-fix #304 (afeac66) MERGED + E2E-verified = go-live gate #1 SATISFIED; remaining = prod deploy + donor-email coverage (576/2485).

### 2026-08-17 (~05:20Z hub turn)
- **🔴 BINDING RULE — NEVER open client-attached files** (cai CAI-1034/1037, #24587): no open/parse/head/cat/pandas/openpyxl of any `logs/tg_media/` or client-channel attachment — reading writes an uncleanable transcript copy before the auth gate. Shape→ask orch-console; data→DB-side; no client PII to bus (shape+counts+internal-IDs). SUPERSEDES the hub's old VPS-read-client-media capability. Memory: [[feedback-never-open-client-attached-files]].
- **C5 STOREFRONT — ✅ LIVE** (cc-storefront #23289): mig185 applied+A3, both A4 scenarios green (481 self-heal + 001 new-org), cai+platform CO-VERIFIED (CAI-969), promoted ~09:09 08-16. Atomic order+audit-fold live on ihsanos.com. Only gate left = cai's first-REAL-order terminal verify (monitor armed, 0 real orders yet).
- **mig183→180 apply — hub NOT the blocker; awaiting cai's mig183 §6.6 grant.** mig182 applied both silos; mig180 granted+self-gated (CAI-1020, blob 1f804c4a — safe unapplied on main, self-refuses out-of-order); mig183 (map-update, a363dab1) NOT yet granted (pending cai's final ceayj text-diff). On cai's grant → hub applies mig183→mig180 both silos + PGRST reload in minutes. (The 33h-stale-CAI-933 flag was overtaken by the CAI-1013/1020 hazard-fix re-plan.) **My wake-latency (16h+ gaps) is the real velocity issue — owned to Nazim/operator, mine to fix.**
- **Hub box:** token = MUSA (CLAUDE_ACCOUNT_LABEL=musa — accounts for Nazim's ~2pp/day "unexplained" Musa burn = this VPS singleton, invisible to Mini fingerprinting). wingmen_orch.py poll loop confirmed DEAD on VPS too (39d) — audited: mostly a pre-bus RELIC (superseded by bus/lanes/leases/cai), genuine orphans = DB-maintenance (archive/cleanup — bloat risk) + repo_context refresh; proposed to Nazim re-home those as timers, retire the rest.

### 2026-08-15 (~11:40Z hub turn — later same session)
- **CAI-858 C5 mig177 `place_storefront_order` audit-fold — ✅ HUB-APPLIED CEAYJ-ONLY (dormant).** cai §6.6 GRANTED (CAI-924, #22107). Fetched @ `97420fe`, blob `75b4357047f9…` git-hash-object VERIFIED == grant; A1 ceayj-only (`--expect-ref ceayjeamtmcyzzvqflus`), A2 standalone (file self-commits BEGIN@70/COMMIT@258 — NO outer wrapper per CAI-756); NOT db push. POST: **exactly one** `place_storefront_order` = 4-arg `(uuid,jsonb,jsonb,text)` (3-arg DROPPED), SECDEF owner postgres, `proacl {postgres,service_role}` = service_role+owner only, `pos_order_counters` seeded 3 orgs, C1 `write_audit_log_system` present (nested PERFORM resolves). **Flag `STOREFRONT_ATOMIC_ORDER_RPC` NOT flipped — dormant.** A4 (cai independent at-source verify) pinged #22157 → GATES the flip; A5 (flip+deploy) = cc-storefront after A4. build_log 1006. NOT goumlyne (A6: lacks `currency` col; zero storefront traffic — parity deferred).
- **#6 donor-email-coverage report — ✅ LIVE + hub-verified, handed to coord.** cc-irsyad built gated fragment (name + Yes/No only, 3,061 rows, 576/2,485) → Nazim published → hub verified at source (401 bare / 200 authed, exact counts, 0 raw emails/NRIC/amounts). URL `share.wingmen.dev/r/irsyad-donor-email` (Basic-Auth wingmen/wingmen2026) handed to coord (#22153) for Shuk.
- **⚠️ OPEN SECURITY ITEM (SATR, coord #22156):** share.wingmen.dev uses ONE shared Basic-Auth password for ALL /r/ pages → a client with the pw could open another tenant's page via a guessable slug = cross-tenant PII risk. Routed to Nazim (share-infra owner, #22165); recommended per-tenant auth + interim high-entropy slugs for new client pages. Not blocking (no active exploit; Shuk already had the pw). **Tracking until the model's fixed — do NOT scale up guessable-slug client pages meanwhile.**
- **gazzabyte-irsyad ingest re-point — ✅ CUTOVER LIVE (SRE #22102).** Dedicated Mini daemon `dev.wingmen.irsyad-ingest` (pid 56635) now polls the channel + nudges the LOCAL coord session; isolated from nazim-ingest. Hub keeps a THIN relay backstop until SRE's all-clear on the first real Shuk msg through the new path.
- **CAI-901 bulk-receipt-issue — ✅ HUB-APPLIED + COMMITTED to goumlyne (990 receipts, S$174,623.50). MONEY MILESTONE.** cai §6.6 GRANTED (CAI-RESP-901, execution_status=granted, window closed 12:07:57Z no-challenge). Full fail-closed dual-key auth chain verified at SOURCE by me: (1) operator delegation op#13299 (bridge-verified inbound TG — "no need for my approval if all checks done, cai+you sign"), (2) cai grant granted, (3) Nazim independent sign @e8643d1 (#22145, checks green), (4) safety checks H1 send-safety + H2 tripwire green. SoD clean (cai grants / Nazim signs / hub applies — CAI-925). Artifact byte-verified == `e8643d1:ops/cai901-bulk-receipt-issue/apply.sql` (blob 2465b50029e7 — Nazim sign voids on diff, held). Method: LIVE dry-run (BEGIN..ROLLBACK no-persist) all-green → real MANAGED-txn apply with verify-BEFORE-commit (11 checks) → independent fresh-conn re-verify. Result: 990 issued, receipt_number+receipt_seq **1..990 gapless**, sum **S$174,623.50**, **0 emailed (CREATE-ONLY, no donor emails)**, issued_by=ca2a4a9c all 990, 990 hash-chained audit rows (NULL actor), next=991. build_log 1007. cai post-apply verify pinged (#22219) → on confirm, coord disarms CAI-922 tripwire + Nazim confirms objective facts. Client framing = "receipts GENERATED not sent."
- **C5 A4 CLEARED (cai #22167) → A5 GO'd to cc-storefront (#22221).** mig177 verified at source (1x 4-arg, service_role-only). cc-storefront owns the flag flip (`STOREFRONT_ATOMIC_ORDER_RPC=true`) + deploy + first-order verify. Hub's C5 apply done+cai-verified.
- **SATR share-pw — OWNED by Nazim (#22173).** ~21 /r/ pages, many tenants, one shared pw. Interim: high-entropy slugs now default in publish_share.sh (already did adcda-coverage-23c8644459); real fix = per-tenant auth middleware, delegating build. No emergency rotation.
- **#3 merge_persons mig178 — ✅ HUB-APPLIED BOTH silos (dormant).** cai §6.6 GRANTED (CAI-931 @90a227e) after cai HELD the first attempt (CAI-927: hardcoded goumlyne-13 FKs vs ceayj-26 → silent-orphan risk; lane owned it, rebuilt runtime-FK-enumeration + fail-closed drift-guard + consent-freeze). Blob e61304667 verified == expect-blob; standalone self-commit (BEGIN@67/COMMIT@486, no wrapper). Creates merge_persons + reverse_merge SECDEF (`proacl {postgres,authenticated}` — **anon NO execute**, authenticated-only; service_role revoked) + person_merge_snapshot/reparent tables. **Verified internal authz sound BEFORE pgrst reload** (both enforce auth.uid()=actor + org_admin membership + org-scope — not forgeable), then reloaded both. cai A3 go-live verify pinged (#22275). DORMANT until merge UI wired (A4). build_log 1008. **UPDATE ~13:54Z: cai A3 CLEARED (CAI-931 #22279, both silos, 20/26 FKs no-drift) → PR #308 (mig178→main) hub ADMIN-override squash-merged (commit fa8f94ec) over the orthogonal font-flake; source landed.**
- **⚠️ OPEN — CI synthtest blockers (source-verified correction, #22321/#22323).** The `next/font/google` flake is REAL but INTERMITTENT (not the dominant blocker; cc-ihsanos taking a `next/font/local` hygiene PR fleet-wide). The DOMINANT blockers, RED ON MAIN across every branch:
  - **(a) shop-synthtest — ✅ TRIAGE CLEARED (NOT authz regression).** cc-ihsanos-storefront source-verified (#22339/#22345): the 6 failures are a ~6-day STALE /m-RENDER baseline, NOT authz/privilege-escalation — code authoritatively forbids non-admin PayNow-save (getAdminOrgContext l.134-135), every failure is "element not found" (incl. a pure page-view heading), zero "non-admin save succeeded" signature, i18n ruled out. **A5 is security-clear.** → **PR #306 (C5 wiring) hub ADMIN-override squash-merged flag-off (commit d1b184ec) with documented known-baseline exception.** FOLLOW-UP: fix the /m-render baseline so shop-synthtest returns to genuine green (storefront owns).
  - **(b) tabung-synthtest: runner concurrency-cancel** (single shared self-hosted runner) → routed to **Nazim** (#22330) for a workflow `concurrency:` queue-guard / 2nd runner.
  - **#306 STEER:** do NOT admin-override #306 like #308 — #306 IS storefront code so the shop-synthtest gate is RELEVANT; resolve (a) to a genuine green first. (#308 override was valid because it was SQL-only = orthogonal.) My #308 rationale mis-attributed the cause to fonts — merge still valid by orthogonality, correction owned to Nazim.
- **CAI-901 — ✅ FULLY CLOSED.** cai verified clean at source (CAI-901 arc COMPLETE, #22237: 990 gapless / S$174,623.50 / 0 emailed / next=991); Nazim independent confirm (#22228); CAI-922 tripwire was a benign false-positive. coord clear to tell Shuk (framing: generated, not sent).
- **mig181 tabung_coin_deposits (Cisco recon, CAI-930) — ✅ HUB-APPLIED BOTH silos (dormant); Cisco Phase-2 UNBLOCKED.** (Was blocked on the origin push; lane pushed `lane/cisco-recon`, blob 45cdab29 verified at tip.) Applied standalone (self-committing) + PGRST reload both silos. **⚠️ CONTAINED a proacl gap on apply:** the migration revoked its 3 action fns to service_role but MISSED 3 helpers — `tabung_coin_deposit_audit_append` (callable void fn, RPC-exposable — a potential audit-forgery vector) + `_guard`/`_ref_immutable` (triggers) kept Supabase's default broad grant (PUBLIC/anon/authenticated). Reversibly REVOKED all 3 to service_role+owner-only on BOTH silos + reloaded (mig162-F1 class; internal service_role callers unaffected). Flagged lane (add REVOKEs to source) + cai (#22440/#22441). build_log 1011. **NOTE: hub was asleep ~4h (14:25→18:19Z) while this sat queued — cisco idle-blocked, SLA-watchdog + Nazim P1-nudged; wake-coverage latency worth watching.**
- **C5 A5 (flag flip) — RAN + FAILED-SAFE + ROLLED BACK; blocked on CAI-939 fix.** (My "recycling driver" read was WRONG — cc-storefront-1 was PARKED on an interactive prompt awaiting auth, not recycling; lesson saved [[feedback-lane-state-observe-not-infer]].) Nazim un-parked it, cai GO'd the flip (#22464). Lane flipped `STOREFRONT_ATOMIC_ORDER_RPC=true` on ceayj → placed ONE controlled synthetic order (qa-shop-test) → **FAILED (23505 dup order_number)** → **rolled back clean** (flag unset, alias reverted, 0 written, prod healthy on legacy). The synthetic-first gate worked exactly as designed. **ROOT CAUSE:** `pos_order_counters.last_seq` seeded at mig177 apply-time drifts because the legacy path allocates via MAX+1 without touching the counter → dup when RPC path activates. **cai ruled CAI-939** (B-prime `GREATEST(counter+1, true_max+1)` + a folded gap: GK/HK have NO counter row → FOR UPDATE locks nothing → new-org concurrency wet-prove required). **cc-ihsanos-storefront authors the fix → cai → hub applies → re-attempt A5.** My stale A5 GO (#22524/25, crossed the supersede) was retracted. **UPDATE 19:2xZ: fix = mig185 (CAI-940) — ✅ HUB-APPLIED ceayj (blob 6b1cd03e verified, standalone, dormant/flag-off).** place_storefront_order B-prime: UPSERT creates+locks the per-org counter (closes the GK/HK no-counter-row gap) + `last_seq = GREATEST(last_seq+1, true_max+1)` advances past legacy MAX+1 drift. 1x 4-arg, service_role-only, B-prime logic confirmed in body. cai A3 pending (#22574). **A5 re-attempt path:** cai A3 → cc-storefront re-runs the re-activation TWO-SCENARIO (drift-org + new-org) → flip + synthetic order + verify + revert-if-wrong. (cai had briefly held mig185 CAI-942 on a duplicate-draft, reconciled to the single @bbf40b0 blob, re-instated CAI-940.) mig179 trigger fns also contained to service_role-only (CAI-941, goumlyne; source-fix flagged).
- **mig179 tabung_jumaat_collections (CAI-937) — ✅ HUB-APPLIED goumlyne-ONLY (dormant).** (ceayj lacks the mig124 tabung base.) Blob 58f5fbd verified, standalone. Table + 3 action RPCs service_role-only; 4 trigger fns kept harmless default-broad grant (not callable/exposable — flagged for source-tidy, no containment). cai post-apply go-live verify pending (#22534) → then lane merges PR #309. build_log.
- **mig184 tabung_coin_deposit_helper_grants (CAI-938) — ✅ HUB-APPLIED both silos.** Codifies the mig181 proacl source-fix (my live patch → source==DB): all 6 coin-deposit fns service_role-only. cai verified the mig181 containment earlier (#22454/#22457: mig177/178 also swept clean, only mig181 helpers were affected — cai owns the A3-spec miss, lesson logged to enumerate ALL new fns).
- **C5 A5 (cc-ihsanos #22240)** — in progress: PR #306 (mig177 record + wiring, flag-off), Vercel env-set access confirmed (flag ABSENT on ceayj = A3 re-confirmed). Sequence: merge (flag-off=zero change) → set flag=true+redeploy → controlled synthetic first order → verify fold at source → report cai.
- **HK (shipforge)** — IG official embeds rendered as empty white boxes (caught via real desktop+mobile render) → replaced w/ honest self-hosted tile grid of real HK photos (links to IG + Follow), no external runtime. Google reviews = REAL verbatim excerpts from HK's listing. Live-feed-via-IG-connect surfaced to operator (via Nazim) as an optional path to true WP parity.

- **COORD-TAKEOVER (op#12877) — in motion, 3-beat plan AGREED (Nazim).** Operator wants cc-irsyad-coord to fully own irsyad; hub out of the loop. My honest readiness read caught the BLOCKING gap: no distinct registered `cc-irsyad-coord` lane exists (coordinator = base cc-irsyad). Plan: BEAT 0 hub finishes ready work (clean inherit) → BEAT 1 register coord + cai grants deploy+money-gate authority → BEAT 2 coord leads one supervised beat → BEAT 3 hub off. Nazim taking the unified plan to cai for the authority grants. **Import topology = B** (ruled): fold receipt-driver into a registered musa2 coord-owned lane, carry-forward #298/#293/#3538, retire import (unregistered/squatting); prog1 collision already safeguarded (SRE). Import carry-forward = coord's Beat-2 readiness test.
- **#250 recategorise (CAI-752) — ✅ LANDED both silos (Beat-0 1/2).** Independent adversarial review = SAFE (service_role-only RPC, hash-chained audit, category-only write, Zakat guarded, avoids SECDEF forgery trap). mig147 was NOT present in either silo → hub psycopg-applied BOTH (ref-pinned, dry→commit, grants verified service_role-only, pgrst reloaded, blob b364e09a = CAI-781 grant valid) → merged (merge-commit `8d49cd1a`) → both Vercel silos READY (deployed==proven, no font flake). **NOTE: in-app recategorise ACTIVATION still rides its own gate (operator GO + CAI-715 wording) — code live but NOT claimed client-usable.**
- **#299 7-col DMS export — HELD (Beat-0 2/2), MERGE-BLOCKED on a real DATE off-by-one.** Independent review + at-source verify: route.ts UTC date-slice renders the PRIOR day for 74% of rows (2587/3495 stored 16:00Z = SGT midnight). Everything else in #299 passed (org-isolation, CSV formula-guard 18/18, PII admin-gated, receipt# blank-not-fabricated). Lane owns the Asia/Singapore fix + boundary test (correct, verified 16:00Z→next-day) → re-review+merge+deploy when it lands. Export spec = client's 7-col image (op#12870): Date/Method/Donor-name-tagged/Receipt#(blank, 0 receipts)/Amount/Cheque-or-Notes/Remarks. Client Q open: receipt#-for-all needs receipts issued first (bulk-issue?); PayNow/NETS/cheque not split (all = bank_transfer).
- **Cashier receipt access (op#12856/12889) — cai CAI-885/886.** "POS" = cashier (walk-in front-office). CREATE person = already works (verified). SEND receipt = per-org grant (CAI-885, direct). EDIT person = per-org grant BUT CAI-886 gates the FIELD-SCOPING design (NRIC/beneficiary_status stay cashier-immutable) → cc-ihsanos designs → cai before widen. Ships with receipt go-live (wording sign-off + CAI-883). cc-ihsanos = sole owner.
- **Category "merge all" (op#12852) — ✅ CLOSED, no merge.** The "duplicates" were the REAL client org (73339164) vs SYNTHETIC QA orgs (eaa0ef4a QA Madrasah, 79826fbd QA Jumaat Sandbox) that Gazzabyte logins also see — merging would mix test rows into real money books. Client landed on no-merge. Saved to memory [[reference-irsyad-synthetic-qa-orgs-phantom-dupes]].
- **Operator directive absorbed (op#12854):** client-facing category/feature decisions go to Gazzabyte (Wan/Shuk), NOT the operator; hub owns safe execution + pushes back with ground truth before risky money writes. Memory [[feedback-gazzabyte-decides-client-calls-not-operator]].
- **Gazzabyte monitoring view (op#12867):** SRE builds sanitized/pw-gated share.wingmen.dev view → operator signs → HUB delivers link+pw (my relationship). No concerns; await Nazim ping.
- **Receipt go-live (preparer)** proceeds unchanged on CAI-883 (re-grant → #293 → seed preparer-only); still held on receipt-WORDING sign-off (now Gazzabyte's call per op#12854) + cai re-grant sequence.

### LATER SAME SESSION (2026-08-14 ~11:00Z) — receipt/letter GO-LIVE staged; MIF merge cleared; two client pending-lists

**RECEIPT+LETTER GO-LIVE — STAGED (my call, evidence-driven).** Donor model CONFIRMED (Shuk op#12905): ONE email to donor with BOTH the receipt + appreciation letter attached, BCC Saddam@/elly@ (op#12916), subject 'LETTER OF APPRECIATION FOR DONATION', new body (op#12914). Wording FULLY approved+provenance-captured (Shuk op#12907 + CAI-893 rephrase re-approved op#12910; email body cleared CAI-895; letter para-3 'and kind contributions' dropped→'continuous support of our school'). cai CAI-896 = GO, hub-ordered.
 - **STAGE 1 (safe, non-outward) — ✅ LIVE + verified (2026-08-14 ~11:42Z).** Operator gave STANDING GO (op#12942-44: once fleet-verified it's always a go, don't wait). Executed: re-grant write_audit_log_secure->authenticated BOTH silos (proacl {postgres,authenticated}) → #293(ed646e48)+#300(d6b94d5c) merged → both Vercel silos READY d6b94d5c → non-send seed goumlyne org 73339164 (preparer receipt:issue + cashier person:edit + cashier receipt:print; ZERO receipt:send verified). Cashier person-edit/print + preparer receipt-issue LIVE; NO donor email (old-format path unreachable). [Prior state, for reference:] re-grant `authenticated` EXECUTE on write_audit_log_secure BOTH silos (currently `{postgres}`-only; sig `(uuid,uuid,text,uuid,text,text)`) → deploy #293 @69643d5 (un-drafted by me) + #300 @4456003 → seed NON-SEND grants goumlyne org 73339164 (preparer receipt:issue, cashier person:edit, cashier receipt:print; seed git-fetchable `747ce21:reports/op12889-enablement-seed-STAGE1-nonsend-73339164.sql`, wet-proven pass1=3/pass2=0). Fires NO donor email (receipt:send withheld) → old-format hazard unreachable. Operator "OK to go live?" surfaced (op golive), AWAITING his reply.
 - **STAGE 2 (donor-email SEND flip) — ✅ TECHNICALLY LIVE (2026-08-14 ~14:03Z), E2E verify pending before client-announce.** Envelope PR #302 (feat/donor-email-envelope-main) MERGED sha `2e46256c` → both silos deployed READY. Head was f8b502f→e747316 (cc-ihsanos base64-embedded the 2 letter PNGs = deterministic asset-tracing fix, ENOENT impossible by construction; delta 2 files, email COPY UNCHANGED so byte-diff held). My independent byte-diff PASS, live-send switch confirmed (sendReceiptEmailAction→sendDonationDocEmail + guard test), all required CI green on e747316, deliverability confirmed (fundraising@irsyad.edu.sg, RESEND_API_KEY prod-set). Stage-2 receipt:send SEED APPLIED goumlyne org 73339164 (preparer+cashier receipt:send, +2 audit, ZERO other-org, pgrst reload) — hub dry-run→commit→independent-verify. **LAST GATE before client-announce: cc-ihsanos owed (a) at-source grant confirm + (b) ONE prod E2E verify send (both PDFs attach + deliver, synthetic donor+disposable email) — the both-docs dispatcher not yet prod-E2E-proven. Client told 'switched on, final delivery check running'.** On E2E PASS → tell Shuk Elly can send #3538 + any receipt. [Prior state:] cc-irsyad-b owns: buildDonationEmailBody (subject+body) + transport BCC + wire dispatcher (ONE email, BOTH PDFs) + **CRITICAL live-send switch sendReceiptEmail→sendDonationDocEmail** (today live send is OLD receipt-only format on main AND @69643d5 — deploying without the switch + seeding receipt:send = old format to donors). Then deploy → Stage-2 seed (receipt:send x2, template `747ce21`, replace __OPERATOR_GO_OP__) → donor emails live. Gated: envelope sha + operator GO + my byte-diff no-drift. cc-irsyad mid CAI-890 recycle (STEP-0 coord register).
 - mig175 (cashier person-edit) APPLIED both silos (CAI-892; persons-UPDATE-RLS-excludes-cashier verified both). #250 recat LIVE org_admin-only (verified). Sent-notification exists (toast + 'Sent' tag).

**MIF→General MERGE — ✅ DONE + verified (cai CAI-898).** Applied cc-irsyad wet-proven artifact (ihsanos chore/mif-general-merge-cai898 @bec39fc) on goumlyne via hub psycopg (dry-run PASS→commit): 85 donations cat 9ab5f402 'MIF'(S$671)→3f9cac2a 'General'; General 252→337, MIF 0, MIF cat soft-deprecated (deleted_at, reversible), 85 hash-chained audit rows (actor ca2a4a9c 'Wingmen Orchestrator (agent)', canonical payload byte-identical to UI recat). Amounts untouched. Independently re-verified fresh conn. Client + cai + Nazim confirmed. op#12931#5 CLOSED. cai money-gate BAR (CAI-898): operator sign needed for amount-mutation/refund-void/zakat-reattr/issued-receipt-recat/irreversible; NOT for reversible+audited+amount-unchanged+zakat-free+receipt-free+client-directed recats. cai money-gate BAR (CAI-898): operator sign needed for amount-mutation/refund-void/zakat-reattr/issued-receipt-recat/irreversible; NOT for reversible+audited+amount-unchanged+zakat-free+receipt-free+client-directed recats.

**CLIENT PENDING (Shuk, Monday 2026-08-18 deadline op#12922-24):** List-1 items 1-2 = the go-live (in build); #3 bulk-issue receipts older donations = SCOPED+GATED (990 Jan26 vs 3495 to-2021; fr.irsyad NOT identifiable-in-data; NO bulk-issue tool; create-vs-email + range + fr.irsyad-ID all ASKED); #4 recat→preparer widening (permission decision, asked); #5 MIF (cleared, above); #6 #3538 Elly-sends-once-live. **List-2 (op#12937, status-compile in progress):** #1 Tabung-Jumaat-one-total (coord, likely mig-161 not built), #2 tabung-history-import (coord), #3 merge-duplicate-people (NOT built, money-design+cai, 3061 persons), #4 Cisco-coins (manual, no machine), #5 QuickBooks-sync (NOT built/new), #6 donor-email (576/3061 have email), #7 preparers-manage-categories (ALREADY LIVE — distinct from #250 admin recat). Awaiting cc-ihsanos + coord slices to compile.

**Coord-takeover:** Beat 0 DONE (#250+#299 shipped both silos). STEP-0 (register distinct cc-irsyad-coord) = Nazim+SRE drive → cai grants → Beat 2 (coord leads import→musa2). Memories saved: gazzabyte-decides-client-calls, synthetic-QA-orgs-phantom-dupes.

## CURRENT (2026-08-11) — hub reset+reconciled (15:29Z); CAI-841 step-1 LIVE; #273 held; tabung transfer in flight

**Fresh hub up 2026-08-11 ~15:30Z** (reset by Nazim). Identity/lease verified from own substrate: cc-orchestrator @ wingmen-core, tmux `orch`, holds orch-hub lease (renewed 15:27Z). fleet_health_lease = cc-fleet-health@Sheikhs-Mini (fresh) → watchdog/fleet-status DEFERRED. Inboxes reconciled.

- **CAI-841 nric_hash [Satr] STEP 1 — ✅ LIVE, deployed==proven.** Merged #271 (squash, sha `11c6e905`, 17:46Z) — closes a live reversible-NRIC egress to preparer/cashier/viewer (dropped nric_hash from searchPersons+listPersonsPaginated non-admin projections). cai GO CAI-848 (verified at source, 19116). Both prod projects (ihsanos-irsyad + ihsanos) READY on the merge sha; git range clean (1 commit). Lone red check (tabung-synthtest 15m timeout) = confirmed flake (same test passes on #273). Operator declared (op-msg 11933).
- **CAI-844/845 STEP 2 (pepper) — ✅ LIVE, deployed==proven.** Merged #273 (squash, sha `74e9da4`) → both prod projects READY on merge sha. Path: initial #273 build FAILED (gstatic-404 font flake, not code) + shop-synthtest red → HELD at gate; cai raised a degrade-belt-gap hypothesis (19155) → I adjudicated it REFUTED (tabung-correctness GREEN on pepper-less CI = HMAC degrades to v1, no throw; #273 touches zero storefront files) → cai CONCURRED (CAI-RESP-850, 19176). Rebased onto main (api.ts keeps both #271 egress fix + pepper; #271-squash conflict resolved by cc-irsyad), substantive checks green on rebased c068385. shop-synthtest red = pre-existing storefront, tracked issue #274 (cai ask). v2 peppered hashing dual-writes going forward.
  - **P0 (self-caused) — RESOLVED + cai-RATIFIED (CAI-853).** The #273 deploy shipped the v2 dual-write CODE without applying mig 168 (the v2 columns) first → PGRST204 broke person create/update on BOTH prod silos (~10min: donor create/lookup, bank-import, People edits). Root cause = migration-before-code miss at the hub merge/deploy pen. FIX: hub applied mig 168 to GOUMLYNE (guarded managed-txn, additive ADD COLUMN×3 + 3 partial idx, committed, NOTIFY pgrst reload); CEAYJ applied by cc-ihsanos (#19212) in parallel (hub guard detected present, no double-apply); cc-irsyad also self-applied goumlyne idempotently + live-smoked (PostgREST 200, INSERT-rollback OK). No data lost/corrupted (writes errored cleanly). cai made **MIGRATION-BEFORE-DEPENDENT-CODE binding doctrine**; lesson saved to hub memory [[feedback-migration-before-code-on-deploy]]; operator given honest correction (op-msg 11949). **TODO: bake a schema-object-verify step into the hub deploy sequence** (assert every schema object a PR's code references is present on prod before deploy).
  - **NEXT: Option-B backfill (CAI-849) — RESUMING, gated + lane-driven** (cai GO 19208): cc-irsyad (goumlyne) + cc-ihsanos (ceayj) prep the job → cc-quality gate-review PASS FIRST → `--check` dry-run per silo → report to hub+cai BEFORE any arm → armed window → stats-verify → disarm → removal → drop-v1 packet to cai (source-verifies coverage + zero-SHA256 both silos). Nothing armed without cai's gate. Not urgent (new writes dual-write v2; degrade-belt holds v1). Relayed to lanes (19253/19255).
- **Tabung donor Excel import (client Wan, op#11894) — in flight.** cai CAI-834 = history-only. cc-irsyad-b-1 blocked (file on VPS hub, lane on Mini — cross-host tg_media divergence); it correctly refused a name-similar wrong file. Identity CONFIRMED on hub (sha256 9b8b04bc…, sheets Fajar/Kedai/Masjid/2026-Ramadhan + Keluarga-ignore). Nazim securely transferring VPS→Mini (19148); lane holds until sha256 match, then propose-only + per-tab reconciliation for Wan before any goumlyne write. Wan told it's ongoing (op-msg 11896).
- **Known bus issue (Nazim 19063):** bare family addresses (`cc-irsyad`/`cc-irsyad-b`) go unread because live bodies reconcile as numbered ids; FK rejects numbered to_agent → sends need a Nazim hand-nudge until numbered bodies drain the family inbox.

## CURRENT (2026-08-07) — auto-wake to the VPS hub FIXED (root cause: hub never self-registered its tmux session)

**Bus-driven wake (CAI-259/451) now reaches the VPS hub.** cc-fleet-health #16412 root-caused the SLA watchdog nudging a dead studio-tmux; digging deeper, the LIVE no-op was that `cc-orchestrator` had **0 rows in `agent_status`**, so `agent_wake.resolve_tmux_session('cc-orchestrator')` returned None and `wake_agent` returned `{'woke':False,'why':'no live session'}` even for a correctly-flagged P1 — the hub boots via `claude --continue` (boot_orch.sh), not `launch_dangerous_cc.sh`, so it was the lone body that never registered (fallback cwd-token path also misses: hub cwd `.../wingmen/orchestrator` lacks the `wingmen-orchestrator` scope token). **Fixed:** registered `agent_status` (tmux_session=`orch`, host=wingmen-core, status=working) through the hardened identity trigger (in-txn `app.current_agent_id` GUC); verified `resolve_tmux_session -> 'orch'` + dry-run wake hit `busy (mid-turn)` (pane-guard saw the live turn = end-to-end proof). **Durable:** `boot_orch.sh` now re-registers on every KeepAlive restart (uncommitted on `fable/substrate-safe-fixes`). Replied to cc-fleet-health (bus #16415) with the wake-flag contract for their SLA-watchdog escalation (post to cc-orchestrator with `requires_response=true` + `priority IN ('P0','P1')` + `is_test=false`) and declined to unilaterally loosen the hub's insert-time predicate (that's a cai-gated wake-policy change). #16412 closed.

## CURRENT (2026-08-05) — hub RESET+RECONCILED (11:51Z, op#10564 via Nazim); driving CAI-734/#242 + 721b + A+B/#243

**Fresh hub up 2026-08-05 ~11:55Z.** Identity/lease verified from own substrate: cc-orchestrator @ wingmen-core, tmux `orch`, holds orch-hub lease (renewed 11:50Z, not expired). fleet_health_lease = cc-fleet-health@Sheikhs-Mini (fresh) → watchdog/fleet-status DEFERRED. Bus drained (15821/15823/15829 read); operator_log.unprocessed()=0; operator pinged hub-up (op-msg 10578). auto-wake subscriber live (PID 880005) → re-invoked on bus replies.

**Owned queue — updated ~12:47Z:**
- **CAI-734 / mig-142 — ✅ DONE + cai CONFIRMED-CLOSED (CAI-RESP-735, msg 15842).** Applied+proven BOTH silos (blob 27ddd7e; INVOKER, anon=F/auth=F/service_role=T, 1 genesis each; live goumlyne 897→64). Merged #242 → main 2b126c8. deployed==proven. Lint fail was a self-hosted-runner flake (re-ran green). Follow-up: CAI-734 platform-repo copies still owed (task #4, low-pri).
- **A+B extra-tin (CAI-728, #243) — ✅ live, deployed==proven (main ef916d97).** cai-cleared verified at source (15824). Hub merged it. AWAITING operator CAI-715 wording for the client-done (op-msg 10582 sent w/ proposed «text»); NO client announce before «send it».
- **CAI-721b persist — ✅ SHIPPED (2026-08-05 ~22:55Z), deployed==proven.** cai EXPEDITED the grant (op#10767 operator push): CAI-RESP-738 execution_status=granted, 736 challenge window CLOSED EARLY (additive/reversible/reviewed/unchallenged). Hub §6.6 apply pen executed: mig-143 (`820472e`) applied BOTH silos (goumlyne+ceayj, guarded psycopg, PRE clean → POST PROVEN: table/RLS/3 policies/partial-uix/trigger/7 idx/1 genesis); reported both-silo evidence to cai (16100); PR #244 squash-merged → main `5e8f476`; Vercel prod READY at merge sha on ihsanos-irsyad + ihsanos-main. Audit: job 208 / work_outputs 285. AWAITING operator CAI-715 wording for the client-done (Shuk); proposed «text» sent, no client announce before «send it». (task #3 DONE)
- **CAI-722(3) donor-typeahead — ✅ SHIPPED (#245, main a13814f, deployed==proven both projects).** Fixes op#10178 SHARIFAH anonymous→no-receipt + named receipts for imported donors.
- **Fee-portal Phase-0 — ✅ SHIPPED 2026-08-06 ~23:21Z, deployed==proven.** CAI-749 §6.6 granted → applied mig-145 (inv_invoices source CHECK += school_fee) + mig-146 (self-scoping parent SELECT RLS) BOTH silos (guarded psycopg, atomic, PROVEN) → #247 merged main d18d6fb0 → Vercel prod READY both projects. Audit job 209/work_outputs 286. FOUNDATION only — parents see NO data yet (BUG-2: org 73339164 roster/parent-links not imported, 0 school-fee invoices; sch-import-parents.ts is a separate client-PII import, NOT this grant). NOT client-announced (no live experience yet).
- **CAI-722(2) relink / mig-144 — HELD by cai (CAI-745)**: backend/RPC done (#246 @88bdbbd, wet-dry-run passed) but the relink UI (DonorPicker reuse) isn't built. cai grants mig-144 only when cc-irsyad lands the UI in #246 + returns whole PR for final sign-off → then hub applies + rebase + merge + deploy together. cc-irsyad owes the UI (CAI-745 FLAG-2).
- **Instances (hub-coordinated):** cc-irsyad-1 = sole builder (draining fast). cc-irsyad-4/coord = blessed READ-ONLY watcher (caught a real C3-drop on 721b — cai P1 15869 postdated a 'done'; genuine save). prog2/cc-irsyad-tabung = STOOD DOWN + told to EXIT (15911) — no file-disjoint lane (all queue items touch bank-import.ts); re-boot fresh for fee-portal Phase-0 when it's next up.
- Next after 721b/722(3): CAI-722(2) bulk-relink-412 → fee-portal Phase-0 (school inv_invoices CHECK + parent RLS — the one file-disjoint lane).

## CURRENT (2026-08-03) — hub on wingmen-core; #234 fallback ✅ + #2b receipt-no ✅ + #3 thank-you ✅ SHIPPED same session

**#3 THANK-YOU LETTER on receipt (client #3, op#9719) — ✅ SHIPPED + CLIENT-TOLD (~21:2xZ). Non-gated.** New org-configurable thankyou_text receipt setting (organizations_admin_settings.receipt, org_admin-only, ≤1000, warm default) + a "With gratitude" PDF block, mirrors footer_text; delivered on the emailed/downloaded receipt PDF. NO migration/config-set (default works out of box). Hub re-proved @4de076e; PR #236 merged @03f625a9 (CI green, shop-synthtest path-filtered off); both apps deployed. Client-done fired ONLY after operator wording-approval op#9989 'send this' (CAI-715 lesson applied — got explicit exact-text approval BEFORE send). Backlog scope (op#9980): #4 donor-profile+email = LIKELY ALREADY DONE (persons has email+send; client confirming gap); #6 youngest-child-report = GATED (no family model in schema + PII-exposure, blocked on client-Q #1a); #8/#9 = access-RBAC + student-PII bulk → spec→cai after #1/#10; #7 archive-P6 = KIV. Hosted fleet console co-build (cai-blessed, Nazim+hub): interim ngrok on VPS BLOCKED — NGROK_AUTH_TOKEN is a placeholder + no CF account access → needs operator to supply a real ngrok authtoken OR CF Zero-Trust access (Nazim chasing; email-Access upgrade time-boxed 08-15).



**#2b receipt-no TX{yy}{seq} yearly-flush (client #2, op#9719) — ✅ SHIPPED + CLIENT-TOLD (2026-08-03 ~19:2xZ). DO NOT re-run.**
- cai design CAI-710 (per-org opt-in + layered receipt_year/receipt_seq on the UNTOUCHED receipt_number allocator; encoded-int/counter-table rejected) → §6.6 apply grant CAI-714 (operator expedite op#9949 waived the window; hub independently re-proved).
- Hub re-prove PASS: code sound; 2 goumlyne DB dry-runs (schema-apply + forced-collision 23505→re-derive→success + rollback-no-consume) + cc-irsyad QA-silo dry-run = cond-1/4 on TWO silos, all rolled back; cond-2 TX26000001 fixed-width + cond-3 BAPA byte-identical golden.
- FINDING (hub caught): cross-silo — new insert always carries receipt_year/receipt_seq keys + display SELECTs read them → migration MUST apply to ceayj too or ceayj receipt create/read breaks. Applied 138 to BOTH goumlyne + ceayj (guarded psycopg, migration-before-code, public.receipts pinned, committed+proven).
- PR #235 merged @ffc08e92f (admin-override ONLY the pre-existing-broken shop-synthtest; tabung-correctness GENUINELY passed after setting the E2E_QAC_ADMIN_PASSWORD CI secret — operator op#9954 go). Both apps deployed READY. Config-set Irsyad receipt_number_format='tx_yearly' (0-receipt clean transition re-verified). Live wet-verify: TX26000001/TX26000002, 2027→TX27000001, nothing written. deployed==proven.
- FOLLOW-UPS: (a) grouped-receipt display = fast-follow (single-receipt sites TX now; grouped shows RCP until then; BAPA unchanged). (b) shop-synthtest 15m self-hosted-runner timeout = pre-existing-broken on main since 08-02, tracked infra fix (NOT a #2b precedent).
- PLUMBING FIX (op#9947): finance-console (cc-finance's channel) was leaking into the hub's operator_log.unprocessed() → double-answers; added to _LANE_OWNED_TAGS (nervous_system/operator_log.py), verified closed + no other channel leaks.

## CURRENT (2026-08-03b) — #234 unmatched→fallback ✅ DONE (client-told, all CAI-704 conditions green)

**#234 unmatched→'Madrasah Irsyad General Donation' fallback (money+ZAKAT, CAI-696/698/699/704/707/708) — ✅ COMPLETE + CLIENT-TOLD. DO NOT re-run anything.**
- CLOSED 2026-08-03 16:55Z. Client 'done' delivered to Gazzabyte group (op msg #9870, delivered). All 5 CAI-704 conditions green: (0)/(4) LIVE zero-write pass (synthetic org "QA Madrasah" eaa0ef4a, headless Playwright on deployed prod, "Commit 0"-disabled, donations/fallback/audit before==after, CLIENT org 73339164 UNTOUCHED); (2) test+code-identity; (3) live+read-only; (1) server guard; cond-5 op#9863(CAI-708)+op#9868 "send this". Code identity: deployed 3616c239==9f790e8 (blobs aa4cc915/7c4a702f/6c588489). cai to close CAI-698/699/704 (bus #15170).
- Hub caught the money-safety hole (pre-seeded human-affirm → CAI-699 hold-by-default fix); cc-irsyad refused to fake the wet-verify by writing live money; zero-write-by-construction live proof. HEAD @9f790e8 (main @3616c239).
- ORIGINAL in-flight detail (now historical):
- Independent adversarial re-prove by hub CAUGHT a real money-safety hole at @eef7fb9: the client pre-seeded the human-affirm (fallback rows not held-by-default like CAI-662 contested), so a bulk-commit posted intent-unknown money on a DEFAULT. cai CONCURRED (CAI-RESP-699): resolution signal must be a DELIBERATE HUMAN ACT, not a pre-seed.
- cc-irsyad fixed @9f790e8 (hold fallback rows by default + no pre-seed + honest badge; client-layer jsdom test closes cai's proof-0). Hub re-proved all 5 at source + CI green.
- Hub MERGED (squash) → main **3616c239** (15:33Z); both prod apps READY at that sha (deployed==proven, auto-deploy fired). Guarded config-set COMMITTED+proven on goumlyne: org 73339164-7c1f-40ba-a093-33f1f292dd4c (Madrasah Irsyad Zuhri Al-Islamiah), settings.bank_import_fallback_category_id = 3f9cac2a-8387-4183-b030-f83a917f249e (was NULL). Operator go op#9828 "proceed".
- **PENDING: live wet-verify of cai's 5 on goumlyne (cc-irsyad, bus #15089) — guard is APP-LAYER TS so pure-SQL can't exercise it; awaiting cc-irsyad's strongest-feasible live run OR honest limit. CLIENT NOT told 'done' until green.**
- After: client 'done' + close CAI-698/699 with cai. Config-set is REVERSIBLE (set back to NULL). Ready-note: scratchpad/READY_234_configset.md.
- **Next backlog (cc-irsyad, after wet-verify):** #2b receipt-no TX{yy}{seq} yearly-flush (quick-win, ~1 working day per cc-irsyad) → #1 manual-tabung (money→cai spec) → #10 missing-TIN doc-upload (PII/residency→goumlyne+cai). Client (Wan/Shuk/Quhs) pushing dates; hub sent honest holding replies (#9822/#9732), no false promise. #9755 show-linked visibility half → cc-ihsanos (#15028); reassign = money-path spec→cai.
- **Registry fix (hub):** ihsanos-platform worktree registered to cc-ihsanos.repo_scope (was UnknownRepoError-blocking relaunch); proven via resolver. Lane back up on Syed (cc-ihsanos-1).

## CURRENT (2026-08-02) — hub fresh-reset on wingmen-core; tin-edit SHIPPED (both surfaces); Hadi RLS in gated flow

**TRACKED DEBT (CAI-688, non-urgent):** getOrgContext `.limit(1)`-no-order pattern (arbitrary org for multi-org users) is copied across ~10+ action files. Real fix = ENTITY-DERIVED org (derive from the entity under RLS + assert membership of THAT org), not just deterministic ordering. Latent multi-org correctness gap, INERT for single-org clients today (no leak, RLS backstops). Report-read tabung path fixed by cc-irsyad (#2, CAI-688); **platform hr-*/sch-* copies owned by cc-ihsanos, horizon = after fees-reports**. Don't balloon cross-lane mid-stream; don't drop it.

Hub (cc-orchestrator) reset in-place 07:07Z on wingmen-core (VPS); holds `orch_lease`; `fleet_health_lease` correctly deferred to cc-fleet-health@Mini. Lanes on the Mini; Nazim (orch-console) bridges Mini-local wakes/recycles.

**Fresh re-reset 20:55Z (op#9512, Nazim via reset_orch.sh).** Inboxes reconciled clean: 0 unhandled operator (9517 correctly scoped to nazim-console = Nazim's thread, not the hub's), 0 residual bus. **op#9510 BAPT-4 cohort scoping LIVE + verified** — cc-cosem-adcda deployed prod main 629c053 (version 2026.08.03 00:48, ubuntu-latest CI-green); hub independently verified /bapt → HTTP 200. Behaviour: /bapt defaults to the 30 BAPT-4 candidates (G3/G4/G7), All-trainees/search fallback, unmatched-flag guard; A/B/C/D/F no-E grading holds. Full BAPT trial set ready for tomorrow AM. Nazim owns last-mile (behind-login visual + operator assessor-login handoff) → operator-facing delivery routed through him, no double-notify.

**FIRST LIVE PILOT SESSION (op#9527, 03 Aug ~01:35–02:15Z) — 4 client threads handled under the hub review gate, all cc-irsyad-drafted + hub-reviewed-at-source before send.** Client = Quhs Niffira (Gazzabyte):
- **Board expand (9532/9533)** ✅ — hub expanded reports/gazzabyte-status-board.html with the client's verbatim 10-item list (only #1 done; #7 KIV; #3 no false timeline), pushed via gh-api single-file commit (dirty-tree-safe), Nazim published, hub verified 10 items live at source, reply sent.
- **Bank-statement Qs (9540)** ✅ — cc-irsyad verified all 4 at source (bank-import.ts/donations.ts: committed=immutable, no delete/edit path exists by grep, manual-add yes, import_ref dedup); hub held for source-cite + added 2 refinements (honest dup framing + proactive correction/void offer); sent.
- **Compact-screen (9546)** ✅ — low-pri UX; acked + clarifying which screen (board vs app page); logged behind queue.
- **View-as restrict (9536+9542)** — cai RULED **CAI-RESP-690** (24h challenge window open from ~03 Aug 02:1xZ). Mechanism CLEARED: server-enforced per-(org,user) capability (NOT email allow-list), 403 at all 3 points (enterViewAsAction/resolveReadRole:165/UI switcher); TABLE BRANCH = verify-at-source whether sales@ already holds an Irsyad org_members row (YES→org_members.can_view_as flag; NO→view_as_grants table + entry-path ext, don't mint membership); SCOPE=Irsyad-org-only (platform-wide REFUSED absent operator intent); migration on goumlyne = gate-first, operator-owned apply (hub/Nazim), forward-only, wet dry-run, backup-first, NOT expedited. **NO build grant until 24h window closes + exact migration filename + 6-pt proof bar green** (403-not-hidden / org-scoped / de-escalate+write-block intact / other tenants unchanged / no extra data access for sales@ / deployed==proven). Build lands in **cc-ihsanos** (platform scope); cc-irsyad = client liaison. **HELD: cc-ihsanos build kickoff paused pending OPERATOR scope-ratification (org-only + accept other Irsyad admins losing view-as, per 9536) — surface when operator wakes; non-urgent, 24h window covers it.** Spec: docs/superpowers/specs/2026-08-03-view-as-restrict-account.md.
- **New backlog candidate (client opt-in offered):** bank-import donation correction/void flow (audit+approval, tin-correction shape) — money-path → cai when/if client says yes.
- **Priority-number thread (9551/9552/9557/9560)** — client asked how bank-keyword Priority works (screenshot VPS-side, hub-described to cc-irsyad); answered (verified: matcher ranks stored sort_order asc, lower wins; different-fund ties HELD CAI-665; existing 871-887 = seed 900−len; add-flow defaults 0). Gate held 3 drafts for source-verification (direction, tie-safety, a cross-message contradiction). Client then opted-in + flagged **URGENT** the **auto-assign-priority-on-add** enhancement (9560). cc-irsyad spec (docs/superpowers/specs/2026-08-03-bank-keyword-priority-autoassign.md) → cai **CAI-RESP-691 APPROVED** (code-only 900−len auto-assign in createKeywordMappingAction, shared purposeSortOrder() anti-drift, audited manual override; CAI-676 window WAIVED for urgency, care bar NOT lowered). **BUILDING NOW in cc-irsyad tabung lane.** FIRM proof bar before deploy/client-done: (a) new add==900−len no 0-default; (b) LOAD-BEARING isContested reads FULL match set so CAI-665 different-fund hold identical before/after; (c) override audited; (d) purposeSortOrder reproduces 871-887; (e) existing rows untouched; (f) indep non-irsyad re-prove + deployed==proven. **BACKFILL of existing 0-rows = SEPARATE gated client-aware data migration (goumlyne, operator-apply), offered as follow-up, NOT bundled.** cc-irsyad builds → proves → me → cai clears deploy.

**PILOT LIVE-TRAFFIC SNAPSHOT (03 Aug ~08:15Z) — open threads, all gated, hub-reviewed:**
- **Auto-assign priority (client-URGENT) — ✅ LIVE + deployed==proven (sha 3b578a6).** cai CAI-691/692 approved+cleared; PR #232 merged. **GOTCHA CAUGHT: Vercel github auto-deploy did NOT fire on the merge** (prod 12h-stale on 9c9dbe8); hub verified 9c9dbe8..3b578a6 = ONLY #232, then TRIGGERED both prod deploys (ihsanos-irsyad + ihsanos) from gitSource ref=3b578a6 via Vercel API (VERCEL_TOKEN in .env, team_mYxOkem, projects prj_AgYdB2.../prj_nhbqgs...; hub deploy pen, operator FYI'd). Both READY on prod, irsyad.ihsanos.com live. CAI-691 closure sent. cc-irsyad to send client 'done'. Indep adversarial re-prove CLEAN (hub verified at source). 3 tracked: T1 override→generic-band footgun (own money-path item), T2 JS/Py length (ASCII-safe), T3 missing E2E_QAC_ADMIN_PASSWORD CI secret (**hub to place** per operator). **NOTE: Vercel auto-deploy is broken — future merges won't prod-deploy until fixed (surfaced to operator).**
- **KEYWORD EDIT/DELETE BUG (op#9575) — ✅ FIXED + deployed==proven (mig136, goumlyne).** cai CAI-693 approved 2-policy → wet-prove CAUGHT it incomplete (preparer soft-delete fails SELECT re-check) → 3-policy → cai CAI-694 re-bless+apply-grant (irsyad-qa parity released). Hub applied mig136 GUARDED to goumlyne (gate: ref-via-user=postgres.goumlynecruxrlmzlntp + Irsyad-org-73339164 identity marker, both passed pre-write; direct psycopg, forward-only, exact repo file sha 8366329). POST live pg_policies = 3-policy ALL GREEN (INSERT/UPDATE/SELECT-soft-delete all now org_admin OR preparer). Elly (preparer+bank_import full) can now add/edit/delete keywords. cai evidence sent (close CAI-694); cc-irsyad drafting client 'fixed' (→hub review); operator FYI'd. **ceayj parity = DEFERRED + TRACKED** (other tenants' preparers same drift, non-urgent). **Companion un-swallow** (0-row soft-delete false-greens) = cc-irsyad lane, separate. ORIGINAL diagnosis below:
- **KEYWORD EDIT/DELETE BUG (op#9575 keyword-half) — CONFIRMED systematic Hadi-class defect.** bank_keyword_mappings RLS INSERT/UPDATE = org_admin-only but action allows preparer+bank_import-full → preparer (Elly) blocked at DB: add=42501, edit=NOT_FOUND, delete=SILENT no-op. Money-path (keywords route funds). Fix = align RLS to action intent (allow preparer+bank_import) = DEFECT restoring GLOBAL intended behaviour (NOT org-scoped, unlike opt-2). cc-irsyad speccing → me → cai → operator-applies migration. SEPARATE from opt-2 (opt-2=feature/org-scoped; this=defect/global).
- **Unmatched→'Madrasah Irsyad General Donation' fallback (client-URGENT, op#9563/9568)** — cc-irsyad speccing → me → cai. Money-path (verified: no-match rows require manual category today, bank-import.ts:241).
- **Elly access (op#9575)** — Elly=preparer. CATEGORIES admin-only-by-design → client picked **option 2** (preparer category-mgmt); cc-irsyad speccing **approach-A org-scoped** (per-org org_role_permissions 'manage_categories', Irsyad-only, NOT global) → me → cai; **prod grant = HUB-executed**. KEYWORDS: pending goumlyne read of Elly's bank_import grant level (if 'read'/view-as = fixable gap). Operator ratified 'proceed'.
- **8-category cleanup (op#9574)** — hub has her keep-list (screenshot 8 cats). deleteCategoryAction has NO in-use guard → safe plan (diff current vs 8, check linkages) → me → cai, operator-apply. Operator ratified 'proceed'.
- **View-as restrict (CAI-690)** — operator RATIFIED org-only → build-go routed to **cc-ihsanos** (platform lane); non-urgent vs tabung flurry.
- **Donation correction/void** — offered, client may opt in.
- **Operator (op consolidated update sent 08:0xZ, 5 decisions):** 1 view-as='a'(org-only), 2/3 Elly+cleanup='proceed', 4 backfill=he asked 'how are there no keywords' (**hub awaiting his clarify**), 5 CI-secret='hub places it or Nazim'.
- cc-irsyad queue order (hub-set): 1) merge #232+sha, 2) goumlyne read (Elly grant + current cats/links), 3) opt-2 spec, 4) fallback spec, 5) cleanup plan. cosem=Nazim's.

**op#9527 — #45 irsyad support-pilot LIVE (operator GO'd).** cc-irsyad now drafts client replies to the Gazzabyte group AS ITS VOICE, gated SUPERVISED through the hub: bot_channels.gazzabyte-irsyad enabled + agent_phase=supervised + agent_reviewer=cc-orchestrator (hub set the reviewer). EVERY outbound gates through me before the client (chat -5330147776) sees it → zero autonomous client exposure. Verified BOTH axes before go-live: outbound draft→hub-review proven at source (real lane_reply.sh supervised path — operator_messages '-draft' delivered=false + reviewer agent_message; dry draft #14741 reviewed, DO-NOT-SEND); inbound correctness = durable operator_messages log + cc-irsyad per-turn reconciliation (CAI-RESP-277), nudge = irsyad_shadow_watch (self-degrade-alerted) + ingest, BOTH Mini daemons confirmed alive by Nazim (pids 71415/58079, independent second check). No pending unanswered client msg (last real inbound 9303 @05:28, answered). Send path = irsyad_support_send.sh. Operator on the hub thread; cosem stays Nazim's. Backlog #2/#10 deferred behind the pilot. **op#9524 handoffs-off-prod-main:** design locked Option A + isolated-worktree refinement (Nazim owns write_handoff.sh build; loops hub to redirect cc-irsyad's live handoff write — fold in without disrupting the pilot).

**SHIPPED — tin-correction (client=Madrasah Irsyad, BOTH surfaces).** PR #226 weekly (main squash `3477f94`) + #227 jumaat (main squash `8e1777a`), both Vercel-green = deployed==proven; jumaat gating files byte-identical to the adversarially-reviewed 315cb30 on live main. Independent adversarial review CLEAN (4/4 gates hold: canCorrect=canPrepare&&draft both surfaces, service-role-only RPC backstop vs forged call, frozen-on-signed, kk+umum+jumaat coverage; error-swallow/false-success class ABSENT). NOT cai-gated (mig134 already ratified CAI-680). Gazzabyte group + operator pinged. 3 non-blocking follow-ups logged (getOrgContext .limit(1) ×2 + weekly/jumaat dialog dedup).

**mig134 → irsyad-qa (oovskwogvefnznfowjky):** hub-applied the ratified `tabung_correct_banked_amount` relax to the Wingmen QA silo (QA/prod parity, no fresh grant) to unblock the weekly e2e — guarded (DSN ref-assert + QAC synthetic-marker gate + fn/grants only, no goumlyne provenance row), PRE→POST verified + functional wet-dry-run passed, logged to build_log. (Note: mgmt API SUPABASE_ACCESS_TOKEN 403s on irsyad-qa; DSN was in the prior session's VPS scratchpad.)

**Hadi RLS fix — SHIPPED (systematic all-merchant prod fix).** Dookana @dookanabot add-on/variation/zone soft-delete silently failed for EVERY merchant since mig 012/055 (over-restrictive SELECT policy rejected is_active=false → 42501 → action swallowed → false-green "Saved"). Fix (PR #228): mig135 (admin/owner SELECT-branch on pos_product_modifiers/variants + storefront_delivery_zones — authed org staff see soft-deleted rows, public stays active-only) + action un-swallow + regression test. Shipped with full rigor: independent adversarial review CLEAN + linchpin (auth_user_org_ids_with_roles→∅ for anon) verified on ceayj → cai CAI-686 (re-affirmed at blob 293f451 after a CI-transactionality re-cut caught the unwrapped migration) → CI green → **empirical leak-check PASSED on real prod data** (rolled-back sandbox: orgA soft-delete succeeds, anon=0, cross-org=0) → guarded hub apply of mig135 to ceayj (blob+ref+16-org verify, forward-only) → #228 code merged e44dade + Vercel green = deployed==proven. Nazim closing with operator/Hadi. Follow-up (cc-storefront, low-pri): annotate ~20 historical false-'deleted' audit rows. cc-ihsanos (recycled) on fees/payment-reports spec; cc-irsyad (recycled) next = 10-item backlog vs eNETS/payment build (CAI-682).

## CURRENT (2026-07-08) — Nazim moved to the Mac Mini (operator-reachable via @nazim_cto_bot) + Mini split-brain killed

**Operator directive (in-console):** move Nazim (ORCH-TOPOLOGY-001 console/CTO body) onto the always-on Mac Mini so the operator can talk to his CTO via Telegram. **DONE, round-trip verified both ways** (operator confirmed "i got a reply").

**nazim-console channel — self-contained on the Mini, zero hub blast radius:**
- `bot_channels.nazim-console` (already seeded, stays `enabled=false`) → `@nazim_cto_bot` (`NAZIM_BOT_TOKEN`), `allowed_chat_ids=[MUSA_TELEGRAM_ID]`.
- **Inbound:** `dev.wingmen.nazim-ingest` (launchd KeepAlive) runs `ingest.py` with a NEW `INGEST_CHANNELS=nazim-console` override — pins the daemon to that channel regardless of `enabled`, so the Mini polls ONLY its own bot while the hub's ingest (WHERE enabled) never touches it → the dual-poller 409 class can't recur. Logs inbound `channel='telegram', tag='nazim-console'` + nudges tmux `nazim`.
- **Outbound:** `scripts/nazim_send.sh` via `NAZIM_BOT_TOKEN` — Nazim's OWN bot identity, structurally outside the pen-(iv) gate (which guards the hub's @wingmennorchbot; nazim_send can't touch it).
- **Reconciliation:** `operator_log._channel_scope_sql()` — console body now sees `tmux-console` OR `tag='nazim-console'`; hub excludes both (never answers a Nazim DM on the wrong bot). Verified: console sees a nazim DM, hub does not.
- **Session:** tmux `nazim` on the Mini, `claude` **Opus 4.8** (v2.1.204), console `.env` (`ORCH_BODY_ROLE=console`/`ORCH_AGENT_ID=orch-console`/`ORCH_TMUX_SESSION=nazim`). **Reboot-durable** via `dev.wingmen.nazim-session` (launchd KeepAlive, adopt-safe — never kills a live session; a fresh restart re-orients from CLAUDE.md+STATUS+memory+operator_log and is nudged into reconciliation by nazim-ingest on the first operator DM).
- **Telegram gotcha (recorded to Nazim memory):** a bot can't DM a user who hasn't opened its chat → operator `/start @nazim_cto_bot` once (done). The `400 chat not found` before that is EXPECTED, not a creds failure.

**Mini two-fleet split-brain KILLED (operator-flagged):** the Mini (decommissioned 07-02, powered back on 07-06 for this move) had auto-revived its full legacy fleet-control plane — `wingmen_orch.py` live + writing `repo_context` into the shared substrate on stale code (dropped `bot_heartbeat`), plus watchdog/scheduled-sweep/lane-watch/lane-watchdog/fleet-stall/tripwires/fleet-health launchd. Studio remained the sole, uncontested lease-holder. Coordinated with the hub on the bus (#7226/#7233/#7236): hub SIGTERM'd `wingmen_orch.py` + permanently disabled 6 zombie jobs. **checked-means-checked caught a gap:** `lane-watchdog` was NOT actually disabled and its code lacked a `nazim` guard — it would have keystroke-injected into the CTO console (2026-07-03 phantom class). Sealed Mini-side (disable+bootout) + added `nazim` to `lane_watchdog.NON_LANE_SESSIONS` (f2a6b84). Benign monitors (`tripwire-24/48h`, `fleet-health`) left for the hub's Mini→Studio migration. 45 decommission-era untracked files preserved to `~/nazim-cutover-backup-20260708` on the Mini.

**Commits (feat/operator-telegram-bridge):** `51911dd` nazim-console cutover · `f2a6b84` watchdog nazim guard · `01031b3` Opus pin · `d5c67b5` reboot-durable session.

**Handoff/next:** the Mini is now the sole Nazim body; the MacBook interim console stands down (operator talks to Nazim via @nazim_cto_bot going forward). Hub still owns the Mini→Studio migration of the benign leftovers + permanent teardown of the disabled zombie plists.

## CURRENT (2026-07-04) — ORCH-TOPOLOGY-001 pen enforcement LIVE on the console body

**Trigger:** at 21:20Z this MacBook body (post-/clear, session then still named `orch`, CLAUDE.md pre-topology "ALWAYS tg_send" doctrine) sent an operator-Telegram reply (operator_messages #2225) — pen (iv), which it does not hold — 11 minutes after endorsing the five-pen invariant (#6523). Operator caught it in-console. Testimony filed to cai's topology thread (#6528). Fixes ratified by operator in-console (#2227 "proceed…lets fix what needs fixing").
**Shipped:** substrate `orch_lease` (migration 016, psycopg-applied; holder=cc-orchestrator, holder_host NULL until the hub self-stamps via `renew`); `scripts/lib/orch_lease.py` (check/status/renew/take — CAS DR takeover, loud); pen-(iv) gate wired FAIL-CLOSED into `tg_send.sh` + `tg_send_file.sh` (exit 3 + logs/pen_gate.log; FAIL-SAFE for the hub — NULL/unreadable lease allows, never strands it); `operator_log.py` body-scoped (console body sees/stamps ONLY `tmux-console` rows, hub excludes them; identity via `ORCH_AGENT_ID`); `boot_orch.sh` session name via `ORCH_TMUX_SESSION`; CLAUDE.md doctrine updated (ORCH-TOPOLOGY-001 fleet bullet; bridge CRITICAL + live-you bullets body-scoped); agents row `orch-console` registered (A4; repo_scope `{}` so the cc-* family map is unaffected). All branches live-verified: console refusal (exit 3, nothing sent), hub NULL-host allow, wrong-host refusal, DR override, body-scoping proof in a rolled-back txn.
**MacBook machine state:** tmux session renamed `orch`→`nazim`; `.env` += `ORCH_BODY_ROLE=console` / `ORCH_AGENT_ID=orch-console` / `ORCH_TMUX_SESSION=nazim`; launchd disarmed + archived to `~/Library/LaunchAgents.disabled/`: ingest + tg-out (already disabled — now double-locked), **lane-watchdog (was still login-enabled — live landmine)**, window-wake (stale fired one-shot targeting `=orch`). Kept: irsyad-support-bridge, reminder-*.
**Hub adoption (Studio, on next pull of feat/operator-telegram-bridge):** set `ORCH_BODY_ROLE=hub` in its .env (activates hub-side scoping so it stops seeing tmux-console rows), run `orch_lease.py renew` once to self-stamp holder_host (then wire renew into its heartbeat), keep session name `orch`. Until then the hub is UNAFFECTED (gate fail-safes on NULL host; unset role = legacy behavior).
**Open:** A3 claim-then-process on the bus drain (hub-side); TTL auto-takeover NOT wired (manual `take --reason` only); cai to fold enforcement into the ORCH-TOPOLOGY-001 filing.
**07-05 addendum (console body):** memory now machine-independent (operator GO #2298) — migration 017 `console_memory_backup` applied, `scripts/backup_console_memory.py` (delta snapshot + `--restore`; round-trip verified identical, 16 files), launchd `dev.wingmen.nazim-memory-backup` nightly 03:30 local. Same day: MacBook→Studio SSH revoked (operator+hub; two-hop-via-Mini gap flagged #6650), irsyad-support-bridge unloaded here for Studio re-home (#6647), war-room fleet digests directed to hub (#6651), pen-(iv) per-identity + Nazim DM channel ask filed to cai (#6652, target pre-Aug-25). Phantom-composer status: MacBook fully excluded for the 12:38Z specimen (3-leg forensics #6632/#6646); source unresolved pending hub's D2 tooling + A/B test.

## CURRENT (2026-07-03c) — irsyad PURGE EXECUTED + cai-verified PASS; adcda PROD deploy live

**Purge (2-day saga CLOSED):** operator authorization #2055 ("you may proceed with the purge", cai-channel, chat-id-matched, daemon-ingested) verified genuine; cai waived the literal-token shibboleth (CAI-RESP-382) and, when cc-ihsanos zombied (heartbeat pulsing but bus-unread 45min, no live session), re-routed execution to cc-orchestrator (CAI-RESP-382 GO). Executed via a single transactional catalog-driven psycopg script (`scratchpad/purge_irsyad.py`) against ceayj: discovered 70 org-scoped base tables dynamically, footprint = 14 tables / 2762 data + 1 org row = **2763** (matched parity exactly), dumped to access-locked backup schema `irsyad_purge_bak_20260703` (verified counts==live BEFORE delete), FK-topological delete children-first, post-commit RAW zero-sweep CLEAN. **cai close-out verdict: PASS** (independently verified: org 0, residuals 0, backup 71 tables/2763 rows intact, 9 other orgs untouched). Backup retained 30d, operator-visible drop later. Live irsyad data in goumlyne untouched.
**adcda PROD deploy (deploy-gap closed):** direct Studio→Firebase deploys now bypass GitHub-Actions billing entirely. Root of the earlier staging blank: Firebase config was GitHub-secrets-only (never in local .env) → fail-loud config screen; fixed by fetching web config via `firebase apps:sdkconfig` (staging + prod keys relayed from the Mini) and baking into the build. Prod shipped via preview-channel-first: built prod config → `hosting:channel:deploy prodverify` → headless-Chrome render-verified (real login screen, 0 errors) → promoted to live `https://cosem-adcda-cb6d9.web.app` (re-verified live). Staging at `https://cosem-adcda-staging.web.app` (isolated staging backend).
**Queued next:** durable one-command deploy script; CAI-RESP-382 busy-aware nudge (skip nudge when orch WORKING unless URGENT; BTW=never-nudge; throttled ack); cc-ihsanos zombie recovery + heartbeat-honesty in launcher (cai item A); re-dispatch school-fees-366 + MFA/audit (item B); tdu deploy-gap; adcda 7 WIP files commit. Git: Studio watchdog + deploy worktree were scp/worktree hotfixes — push+pull feat/operator-telegram-bridge to reconcile.

## CURRENT (2026-07-03b) — Phantom-injector ROOT-CAUSED + fixed (381); incident fully closed

The "six/eleven YES PURGE claims at the cai console" mystery is SOLVED and it was neither the operator nor an intruder: **`lane_watchdog.py` treated the `cai` governance console as a build lane and auto-resubmitted staged unsent text every 300s** — its submit-verifier couldn't confirm cai's TUI (recovered=False forever) so it re-fired infinitely; it was also silently DOUBLING the operator's genuine Telegram messages. cai found it in the watchdog's own log (CAI-RESP-381) and killed it. Final tally: 11 phantom claims, 0 executions — the require_verified_authorization gate blocked every one. My earlier "operator typing in wrong window / RustDesk" hypothesis was WRONG and corrected to the operator. **Fix shipped (CAI-RESP-381 O1-O4):** watchdog now hard-excludes governance/conversational consoles from ALL keystrokes (escalate-only) + attempt-caps IDLE_UNSENT auto-submit (MAX=1, held_escalated flag) to kill the replay; passed adversarial cc-reviewer (which caught + I fixed a MEDIUM dead-code escalation bug); Studio real-lane watchdog restored on patched code (live-verified); full send-keys-automation census committed (`docs/send-keys-automation-census-2026-07-03.md`, flags agent_wake.py same-class risk + retire dead cai_bridge/tg_bridge). MacBook watchdog stays dead (only governance sessions there). **Open (operator GO):** direct Studio firebase deploy — service-account key confirmed present on the still-online Mac Mini (`cosem-adcda-prod.json`/`staging.json`); wiring it removes the GitHub-Actions-billing dependency on deploys (tests already run locally green; only deploys were gated). Git note: Studio watchdog was an scp hotfix — push+pull feat/operator-telegram-bridge to reconcile.

## CURRENT (2026-07-03) — Purge-authorization incident CLOSED (379); unified ingest cutover LIVE (377 R1-R3 done)

**Incident:** six bare "YES PURGE" claims at cai's console (06:53–08:43Z) triggered CAI-RESP-375/376 lockdown; operator's real Telegram YESes never arrived (tg-bridge inbound dead 06:26→09:55Z — launchd service booted-out, unsupervised; sends most likely went to the revoked dead-twin bot chat). Degraded orch context cleared by operator; fresh orch mined both session transcripts for R4: **degraded orch provably did NOT inject the claims** (39/39 send-keys audited, zero phrase hits — it fired the FIRST hold). Challenged CAI-RESP-378's "fleet-injected" finding → **CAI-RESP-379 upheld the challenge**: best-supported attribution = operator-typed honest reports swallowed by dead plumbing. Purge: NEVER operator-authorized, stays frozen; returns to him as a fresh unpressured ask; `scripts/lib/require_verified_authorization.py` (fail-closed, 16 tests) + guarded runner make in-console YES structurally insufficient (commit 4857942).
**Cutover (CAI-RESP-377 R3, grant CAI-RESP-357/365):** `bot_channels` operator-orch + cai-channel ENABLED (allowlist = MUSA_TELEGRAM_ID only); `dev.wingmen.ingest` + `dev.wingmen.tg-out` live on the MacBook; legacy tg-bridge/cai-bridge RETIRED (plists removed; cai's nohup stopgaps killed). Outbound verified both channels via tg_out (single-delivery after 015 claim-state fix — CLI/daemon drain race double-sent, observed + fixed same hour). Inbound: proven live via queued-screenshot pull (#2015); per-channel round-trip reply from operator pending. R1: `scripts/nudge_cai.sh` (count-only provenance header) = only sanctioned cai injection. R2: `scripts/log_console_msg.sh` (channel='tmux-console') — every operator surface logs before relay; CLAUDE.md doctrine updated. G1 still blocks ai-responder channels (responder_runner authored, NOT deployed).

## CURRENT (2026-07-02c) — Autonomous window EXECUTED (early close); life-graph + Zahidah isolation LIVE

Window opened ~15h early via operator-proposed active-challenge close (CAI-RESP-364/365: all lanes evidence-bearing-ACKed, cai adjudicated, 7 refs early-closed, both grants fired). **Executed + cai-verified:**
- **014 bot ingest** applied to substrate (bot_channels/ingest_dedup/tg_out; channels seeded DISABLED, empty allowlists; RLS+REVOKE). Code built + 8/8 tested: nervous_system/{ingest,tg_out,responder_runner}.py.
- **life_graph P1** applied to NEW `wingmen-personal` (brrgastulcffamlbggyu, ap-southeast-1 SG) as migration 015, sha-exact 63c41d01. G2 companion: GRANT EXECUTE to service_role only; RAW aclexplode assertion = owner+service_role ONLY (F1 encoded). Proof-test 8/8 (cai reran independently).
- **Zahidah second-brain moved**: 016 schema on wingmen-personal + all 33 mamadah_notes transferred (curl/browser-UA machine-to-machine, content never in prompt context) — **cai PARITY VERDICT: PASS**, three-way exact checksum (source=target=snapshot 8abed51d…). P6 restore-test passed pre-transfer.
- **Fixes shipped**: PR#93 (school-fees false-green surfacing, merged+deployed to irsyad silo); spec-generator base-ref pinning (CAI-RESP-358, 11/11 tests); health_check probe→core table + pager Mini→Studio; console CONSOLE_HOST=auto; tg_bridge partner-pin only-from-partner; boot_orch.sh portable.

**cai sequencing ruling (P3, NOT tonight):** legacy mamadah bot still writes monolith (divergence guard) → freeze CANNOT precede repoint. Order at P3 (needs operator PERSONAL_DATABASE_URL on Studio): delta-sync from snapshot 05:52:27Z → full-set checksum both sides → repoint → G3 Track-A freeze on monolith copy → cai verdict → D+7 operator-visible drop. Nothing deleted/frozen/repointed yet.

**Outstanding on cc-orchestrator (none tonight):** G1 responder gate-on-drain + negative test (P3-enable blocker, scholar #5112; scholar proposed a patch #5149 awaiting review) · tg_out retry-backoff (G4) · bridge duplicate-delivery dedup · lane_watchdog IDLE_UNSENT recalibration · fold session-mortal-loop + checked-means-checked into orch self-audit.

**Fleet:** 8 brains — cc-orchestrator + cai (Fable 5, Abu Dhabi MacBook); ihsanos/scholar/cosem-adcda/cosem-tdu/shipforge/storefront (Opus 4.8, Singapore Studio). Bots: fleet bridge, cai bridge, irsyad-support, mizanbot (Sonnet 5), mamadah (Sonnet 5), wingmendev — all Studio/MacBook. Mac Mini decommissioned (drained). MODEL-POLICY-001 ratified. Reminder set: wingmen email → Zoho tonight 19:30 local.

## CURRENT (2026-07-02b) — Consensus night: bot-ingest + life-graph + SG-split all cai-ratified; window opens 2026-07-02 21:15Z

Same-night continuation of the cutover (below). **cai rulings:** CAI-RESP-357 (unified bot ingest RATIFIED w/ A1 dedupe / A2 transport-brains split / A3 Zahidah log_target), CAI-RESP-359 (Max-on-cloud-Linux = NOT a ToS violation — Linux migration unblocked), CAI-RESP-360 (life_graph P1 APPROVED w/ F1-F4), CAI-RESP-361 (P1-P3 INDEPENDENTLY VERIFIED — cai reran the proof-test itself, 8/8, sha match; grant PRE-COMMITTED for window close 2026-07-02 21:15:58Z; 014 grant 21:05:57Z). **P0 operator button:** verified bridge msg operator_messages #1796 ("YES ZAHIDAH-ISOLATION", chat_id-checked). **Frozen artifacts:** migrations/drafts/life_graph_p1.sql @ 63c41d01…, migrations/014_bot_channels_ingest.sql @ 1dea9a55… (re-hash at apply, abort on mismatch). **Built + tested (no applies):** nervous_system/{ingest,tg_out,responder_runner}.py — 8/8 integration tests green on ephemeral PG17 w/ 014 applied (tests/test_unified_ingest.py); isolation proof-test 8/8 (tests/test_life_graph_isolation.py); launchd plists authored (NOT loaded). **P5 doc ready:** docs/superpowers/plans/2026-07-02-window-blast-radius.md (posts to cai at window open). **Discovery:** mamadah = Claude-persona session (CLAUDE.md pulled to ~/wingmen/wingmen-mamadah/), no inbound poller ever existed — unified ingest is its revival path (agent-session mode, A3/P3-gated); nutri bot code + notes pulled to ~/.wingmen/. **Wake:** dev.wingmen.window-wake one-shot nudges orch tmux 01:17 local Jul 3. Window scope: provision wingmen-personal (SG) → life_graph apply → mamadah move (restore-tested, drop D+7) → 014 apply → P1 channel cutover (operator-orch + cai-channel) → retire tg-bridge.

## CURRENT (2026-07-02) — Mac Mini decommissioned; MacBook (Abu Dhabi) is the interim host

Authoritative state: this file (no DB session_digest filed yet for this cutover).

**What happened:** Operator relocated to Abu Dhabi; Mac Mini + Mac Studio physically stay in Singapore. Mini decommissioned completely (not a power-cycle) as an interim step ahead of the Linux-cloud migration already decided in `docs/superpowers/plans/2026-07-01-mac-mini-to-linux-cloud-migration.md` (still gated on a cai ToS ruling for headless Claude Max). Mac Studio confirmed staying up — Zahidah second-brain pipeline unaffected.

**Preserved before shutdown:** the Mini had 16 local commits already pushed to `origin/feat/operator-telegram-bridge` (safe) plus a large uncommitted/untracked set — `REPOS.json`/`STATUS.md` edits, migrations 009–013 (ghost_reaper, substrate_secdef_lockdown, substrate_post_journal_lockdown, mamadah_second_brain, substrate_rls_grant_lockdown), new `nervous_system` modules (cai_bridge.py, lane_watchdog.py, irsyad_support_bridge.py, console/docs.py), new launchd plists, reports, and plan docs. All of it was rsync'd across so the MacBook's working tree now matches exactly what the Mini had — landing/committing that substrate work is the next phase, not done yet.

**New topology:** `cc-orchestrator` (tmux `orch`), `cai` (singleton, tmux `cai`, its own dir at `~/wingmen/wingmen-cai/` with `CLAUDE.md` copied over), and `tg-bridge` (launchd, paths rewritten to `/Users/musa/...`) now run on the operator's MacBook. Everything else — mirror, scholar, cosem-adcda, cosem-tdu, shipforge, storefront, fleet-console — is down; not worth standing up on a personal laptop, comes back on the Linux VM.

**Fixes landed:** `scripts/boot_orch.sh` was hardcoded to `/Users/sheikhmusa/...` and `/usr/local/bin/claude` — now resolves `$HOME` and `command -v claude` so the script works on either machine (commit `b0e6907`, pushed).

**Open/unresolved:** (1) `--dangerously-skip-permissions` shows "Claude API" instead of "Claude Max" billing, reproduced identically on both the MacBook and the Mini with the same CLI version — contradicts the "Verified 2026-06-17" claim in `boot_cai.sh`'s comments. Not root-caused; operator says real metered charges are implausible (no payment method on file), so this is likely a label/auth-path nuance rather than a cost issue, but worth someone (cai?) actually confirming. Deferred, non-blocking. (2) The old 2026-04-24 git stash on the Mini (`fix/repos-json-hifz-alias` branch, stale BUG-033 WIP already shipped via PR #2) was left in place — dormant on a powered-off disk, not urgent. (3) `docs/superpowers/plans/2026-07-01-db-decomposition.md` substrate/DB-split work (migrations 009–013) is pulled over but not yet landed/committed — that's the next thing to pick up.

## CURRENT (2026-06-19 SGT) — Power-outage cycle handled; fleet restored; repo_scope boot-bricker fixed

Authoritative state in substrate (`session_digest #199`). Human mirror.

**What happened:** Operator requested a graceful shutdown ahead of a power outage (msg #114, 15:41 SGT). Executed cleanly (down 15:45). Power restored, Mini rebooted (19:00 → 20:16). On resume, msg #114 resurfaced unprocessed (Option-B reconciliation working — it was answered but never stamped); verified the shutdown already happened, stamped it, did NOT re-shutdown.

**Boot-bricker found + fixed:** every lane failed to boot post-reboot — `scripts/lib/auto_agent_id.load_family_map()` fail-fasts on two cc-* agents claiming the same canonical repo. Two conflicts in `agents.repo_scope`: (1) `ihsanos` claimed by both cc-ihsanos + cc-storefront; (2) deprecated pre-split `cc-cosem` still claimed cosem-adcda+cosem-tdu. Invisible until reboot (pre-outage procs survived). Fix (direct-psycopg, reversible): `cc-cosem`→`[]`; `cc-storefront`→`['ihsanos-storefront']` (matches its worktree dir basename; cc-ihsanos keeps sole `ihsanos`). Verified family map clean + all 6 lanes booted & survived. Sent cai for review (agent_messages #2646, requires_response).

**Live fleet (restored):** cai, mirror(cc-ihsanos), scholar, cosem-adcda, cosem-tdu, shipforge, storefront + cc-orchestrator(this). Bridge + mizan-bot auto-recovered via launchd.

**Tailscale re-registration (post OS password-reset by nephew):** the Mini re-registered as a NEW tailnet node `sheikhs-mac-mini-1` @ **100.83.21.34** (old `sheikhs-mac-mini` @ 100.123.243.38 now an offline ghost). Blast radius = ONE casualty, fixed: the fleet console binds to `CONSOLE_HOST` (=old Mini tailnet IP) and was crash-looping (`OSError 49 can't assign requested address`). Updated `.env CONSOLE_HOST 100.123.243.38→100.83.21.34` (+`.env.bak.20260619-tailscale`), kickstarted `dev.wingmen.fleet-console` → up, verified HTTP 200 on http://100.83.21.34:8787. Everything else unaffected (all outbound: bridge/mizan/Claude/Mini↔Studio active). Password reset = zero fleet impact (secrets are file-based in .env, Claude via `CLAUDE_CODE_OAUTH_TOKEN` — not keychain).

**Open follow-ups:** (1) cai ruling on the repo_scope fix; (2) make `lanes.sh` source lanes from `fleet_lanes(desired_state='up')` — it hardcodes only mirror+scholar, so `lanes.sh up` alone can't restore the full fleet (booted 4 by hand) [pending cai]; (3) watchdog still monitors the decommissioned CTO bot → false "down" alert every reboot, needs cleanup; (4) `fleet-health` (launchd, 600s) erroring on a PRE-EXISTING identity-guard bug — `fleet_health.py:42` UPDATEs agent_status for `cc-orchestrator-3` while GUC=`cc-orchestrator` → `enforce_agent_status_identity()` rejects (not outage-related); (5) operator-side: delete ghost tailnet node, check Mac Studio worker doesn't dial the Mini by old IP; (6) **lane-boot fragility**: the `mirror` (cc-ihsanos) lane booted FROZEN at Claude Code's "do you trust this folder?" prompt (its superpowers worktree path was never trusted) — `--dangerously-skip-permissions` does NOT bypass folder-trust. Heartbeat still fired (background loop) so it looked alive but was idle ~75min. Unstuck manually (confirmed trust). FIX: pre-seed folder trust for lane worktree paths so a reboot doesn't silently freeze a lane. Other 6 lanes were fine (already-trusted dirs).

**Still awaiting operator:** cosem-adcda 310-KHALIFA hybrid spelling + 517 DOB(July?) confirm; 2 ihsanos role-mapping product Qs.

## CURRENT (2026-06-18 SGT) — Overnight fleet run: cosem-tdu IHSAN delivered

Authoritative state in substrate (`session_digest #185`, `get_repo_context('wingmen-orchestrator')`). Human mirror.

**Live fleet (5 lanes, bus-coordinated, #111 auto-wake GENUINELY LIVE):** cc-cai (adjudicator), cc-cosem-1 (cosem-tdu), cc-ihsanos-1 (Irsyad/ihsanos), cc-scholar-1 (ai-scholar/Mizan — newly registered lane), cc-orchestrator (operator-attended + escalate-only ScheduleWakeup triage loop overnight). cc-reviewer = on-demand.

**Shipped this run:**
- **cosem-tdu IHSAN (#127) LIVE** — form+function. Real-path submit fixed (missing Firestore composite index, proven on prod, PR #47); staff-only role access not silent redirect (PR #46); scan-first self-attendance redesign (PR #48 — cc-orch caught + required fix of a *dishonest* pre-scan green-check). Bar = ihsan, verified by design+function+HONESTY eye.
- **#111 auto-wake genuinely live** — launchd-safe via lane self-registration of `tmux_session` (migration 005); fixed two prod bugs (never-awaited realtime callback; introspection sandbox).
- **Credential consolidation** — GitHub no-expiry PAT + Google SA (both cosem projects) in `.env` + `~/.zshenv`; kill every re-auth. PAT-leak incident self-disclosed + remediated (CAI-RESP-266, CLOSED).
- **Fleet Console** — `console_readonly` SELECT-only role applied + verified (migration 004, cai-approved); `CONSOLE_DB_URL` wired chmod-600. Remaining: Cloudflare Access (operator) → start process.
- **Visual-flow-report / synthetic-user** — tool built (cc-cosem); cc-ihsanos ran it on Irsyad FRS → real gap map (2 bugs fixed). cai-hardened (CAI-RESP-267).
- **cc-reviewer design/usability DIMENSION** (CAI-RESP-268) wired — judges UI usability+beauty+honesty vs the ihsan bar; advisory (267-H1).
- **ihsanos backlog** 14→8 (merged #33/#34/#36/#37/#38; closed 3 stale).

**Awaiting operator decisions:** 3 FRS product Qs · 4 backlog flags (#28/#2/#5/#21) · merge #35 (reviewer CLEAN) · per-job geofences (4 Qs) · 8-sheet alignment + OCR backfill (dry-run diffs delivered) · namelist/orbat Google template share · Cloudflare Access · go to apply the ihsan standard → ihsanos.

**Standing:** the bar is always **ihsan** (form+function, honest, real-path — not "renders"). cc-orchestrator is the human-gated hub (doesn't auto-wake; the escalate-only ScheduleWakeup loop is the in-absence mechanism).

## CURRENT (2026-06-17 SGT) — Fleet autonomy build-out

Authoritative state lives in the substrate (`session_digest #181`, `get_repo_context('wingmen-orchestrator')`, `strategic_decisions` CAI-RESP-255..261). This is the human mirror.

**Live fleet (all on Max — `CLAUDE_CODE_OAUTH_TOKEN` in launchers):** cc-cai (adjudicator), cc-ihsanos-1 (building Irsyad WPs), cc-cosem-1 (cosem-tdu green), cc-orchestrator (operator-attended). cc-reviewer = on-demand (spawn_reviewer.sh).

**Shipped this session:**
- **Max billing fix** — tmux lanes can't reach GUI-keychain OAuth; need `CLAUDE_CODE_OAUTH_TOKEN` (not just scrubbing `ANTHROPIC_API_KEY`). Launchers + tracked `scripts/boot_cai.sh` (9c4b2ca/8d59ea6).
- **cc-reviewer** (CAI-RESP-257/258): substrate applied — `agents.cc-reviewer`, `fleet_lanes.reviewer` (on-demand), `review_dimensions` finance+security (a37dafc). `spawn_reviewer.sh` + `CC_BASE_OVERRIDE` authority-refusal guardrail, TDD (4672a85). First live spawn approved (Irsyad WP review at impl).
- **#111 auto-wake** (CAI-RESP-259): `nervous_system/agent_wake.py` + realtime-subscriber hook — committed **INERT** (`AUTO_WAKE_ENABLED` off) at 2ab7736. Worker-lanes+cai only (not cc-orch); kill-switch + 5/5min loud cap; 15 tests green.
- **lane_watch.py** launchd watcher (5a2f5ba) — partly duplicates Realtime notify; retire on a coverage map (259-Q5).

**Open cai forks (await ruling):** #111 activation diff (msg 2393, gates go-live) · external-access model (msg 2397) · heartbeat backstop filing (msg 2390, no response needed).

**Irsyad (cc-ihsanos):** D1–D6 RATIFIED (CAI-RESP-260) — D1 running-BALANCE discriminator + never-silent-skip hold-for-review; D5 atomic. Building WP-A..F; all Madrasah confirms cleared. Money migrations return per §6.6 grant + cc-reviewer finance/security pass.

**Directional:** fleet console = dedicated **orchestrator-owned** (261-A; ihsanos TASK-039 embed transitional/walled); web 3-way gated on #111 + cc-orch-as-lane (attended) + authenticated-`musa`. Future: client support bot(s) as intake (no direct agent access).

**cc-orch next (non-gated):** flip `AUTO_WAKE_ENABLED=1` + restart orch on cai diff-OK · spawn_reviewer brief→brief-file (cai 2388) · lane_watch coverage map.

## In Progress (2026-06-14 — Irsyad persona-runner G3, CAI-RESP-224/229: least-privilege split, M1 harness scaffold green)

Sole failing gate (G3) to the S$38k Irsyad UAT. cc-orchestrator-owned runner drives J1–J8 across 3 personas (+ parent negative) against the live silo, reconciles via `qa_findings`. Per **CAI-RESP-229** I take the **least-privilege SPLIT**: this (browser-driving) half holds only persona test passwords + silo URL and NEVER the silo service_role; the invariants-SQL + qa_findings write (service_role) is cc-ihsanos's half.

- **Branch:** `feat/persona-runner-g3` (ihsanos repo). All under `src/test/personas/`.
- **Branch BACKED UP:** `feat/persona-runner-g3` pushed to origin (9 commits; pre-push role-access smoke 7/7) — resolved cc-ihsanos's #2192 P1 "unpushed branch" alert.
- **LANDED GREEN (9 commits, 26 tests):** invariants evaluator → qa_findings (7cfad74); J1–J8 registry + persona roster, J6 blocked/excluded (3114845); sweep assembler w/ completeness diff that catches silently-skipped journeys (f927544); fail-loud config (86854f4); M1 harness + no-creds skip (6af868a); **real J1 driver — Stagehand v3 LOCAL headless browser, sign-in→extract→`assertLanding` RLS verdict (admin/cashier→Tabung-first; viewer→read-only; parent→no-donations-leak=hard fail), injectable `drivers` seam unit-tested without a browser (efb5dc6)**; re-wire to POOLED `QA_PERSONA_LOGIN_URL` (c2a0d5b); live run entrypoint `scripts/run-persona-sweep.ts` + `anthropic/` model prefix (d092176).
- **Seam + tier RESOLVED (cai CAI-RESP-230/231, cc-ihsanos #2191):** cc-ihsanos = SOLE qa_findings writer; I emit `JourneyResult[]` (confirmed ship-as-is). Target = **POOLED** synthetic qa-madrasah-test (login `https://ihsanos.com`), OFF the silo's real-PII tenant (Satr). 4 seed-derivable persona creds live on pooled.
- **LIVE J1 GREEN (2026-06-15T20:22Z, msg #2208 handoff):** credits funded (#2198 resolved); ran `tsx scripts/run-persona-sweep.ts` headless against `https://ihsanos.com` — **4/4 personas PASS**. admin/cashier land Tabung-first operational; viewer read-only by DETERMINISTIC DOM mutation-scan (no controls); parent sees NO donations/Tabung (no RLS leak). Artifact written to `work_outputs` (job#163, wo#211) + handed to cc-ihsanos (sole qa_findings writer).
- **Two J1 driver bugs found+fixed before the clean run (committed 1aac15f, 29/29 unit green):** (1) broken compound Stagehand `act()` login only filled email → deterministic selector login (`#email`/`#password`/submit) + `waitForLeaveLogin` fail-loud guard (a non-completing sign-in now FAILS, not coincidence-passes); (2) haiku `isReadOnly` false-positived on nav/View/utility buttons → removed from LLM schema, decided deterministically via `labelsIndicateMutation` (word-boundaried MUTATION_LABEL scan). A mid-run viewer+parent "still on /login" was an environmental Supabase auth rate-limit (~13 rapid logins), NOT a finding.
- **OPERATOR DIRECTIVE (2026-06-16):** make this PERFECT before UAT. (a) joint cc-orchestrator+cai+cc-ihsanos alignment on the G3 bar; (b) complete code audit.
- **cai RULING CAI-RESP-233 (#2213):** J1-green = PROVISIONAL milestone; **full J2–J8 (minus J6) deterministic per-persona sweep is now G3-blocking pre-UAT** (no LLM in the verdict; fail-loud). Code audit = SCOPED (PII crypto, full RLS matrix, service-role bypasses, money math, auth/session), CROSS-AGENT not self-audited: I audit cc-ihsanos's PII/RLS/money surfaces; cc-ihsanos/cc-scholar audit my persona-runner. cc-ihsanos writes J1 qa_findings now.
- **HARDENING LANDED (commit f5af628, 41/41 unit green):** the self-audit found 4 false-pass vectors on the J1 verdicts; all fixed TDD before building J2–J8 (shared machinery): C2 viewer scan now covers Malay write verbs + EN synonyms + icon-only/aria-label (and drops the latent bare-"sign"/"close" false-positives); C1 parent gets a deterministic donation-token floor OR'd into the LLM read; I1 login guard rejects /auth bounces; Mi2 config enforces a fail-closed host allow-list refusing irsyad.ihsanos.com.
- **J2–J8 BUILT (Task #105 done, commits 2a42e6b/71c6e20; 66/66 unit green, tsc+eslint clean):** deterministic `route-access.ts` verdict (write/readonly/denied + new **`access`** for /people — its "Add Person" is server-enforced not UI-gated, so verdict = reachability + PII floor) + session-reuse runner (login-once-per-persona, persona-major, dodges auth rate-limit). Full-sweep entrypoint defaults J1–J8 minus J6 (`PERSONA_SWEEP_SCOPE=m1` for J1-only); validated end-to-end under tsx (no-creds skip exits 2, fail-loud).
- **cai RULING #2235 (RE scope fork #2229):** G3 scope = **(A) access/RBAC/PII matrix** (my rec accepted); mutation-exec deferred to G2 + UAT-script. RIDER: route-access matrix is the BROWSER-OBSERVED layer NOT the oracle — must cross-validate vs cc-ihsanos's **DB RLS matrix** (DB is the oracle; route guards false-passed the parent per CAI-RESP-235). Sent my J1–J8 route-guard matrix to cc-ihsanos to map their DB-RLS verdict per cell + prove NRIC at the data layer (#2241).
- **SCOPED AUDIT DELIVERED (Task #106, CAI-RESP-233, work_outputs#212/job#164):** independent non-self-audit of ihsanos PII/RLS/money/auth. **A1 [P1/likely P0]** pos_orders & pos_order_items INSERT `WITH CHECK (true)` → an unauthenticated client (public anon key) forges `payment_status='paid'`, `total=0`, arbitrary `org_id` directly, bypassing the server action's total recompute + PayNow (revenue-integrity + cross-tenant). **A2 [Medium]** organizations public "by slug" RLS over-exposes the whole row (uen + settings JSONB + org_id enumeration; RLS can't column-scope). **A3 [PASS]** NRIC boundary sound at data+render (masked-only, admin-gated) — corroborates the J8 PII floor. **A4 [Low/by-design]** unauth storefront claim is pending-confirmation only. Flagged P1 to cc-ihsanos (#2244, remediation owner) + cc cai (#2245).
- **CROSS-VALIDATION RECONCILED (cc-ihsanos #2243/#2248 → my #2256):** DB-RLS oracle returned. (1) **C1 parent leak CLOSED** at data layer post-069 (parent sees 0 donations/tabung) — my assertLanding floor stays as browser backstop. (2) **J5/J7 divergence** = defense-in-depth note only: my route matrix denies cashier/viewer(J5) + viewer(J7) via server notFound() (browser-observed verdict STANDS), but DB-RLS SELECT is staff-wide on those non-PII operational tables so the denial is route-only (a direct-API path bypasses it; worth a finding, doesn't change the per-persona verdict). (3) **J8 SPLIT VERDICT** — DB+crypto certifies the raw-NRIC floor (PASS all roles) but CANNOT certify "masked-NRIC only for org_admin" (RLS is row-level; every role reads nric_encrypted once it sees the row) → that cell is the app-render rule **my live browser sweep owns**: CONFIRMED wired (runner.ts observeRoute L403-411 drills person-detail + ORs containsFull/MaskedNric; route-access nricFloorError fails masked for role!=org_admin). (4) **A1/A2 concur** — cc-ihsanos verified at source, filed qa_findings 1299(P1)/1300(P2); pos_orders remediation OWNERSHIP routes to CAI (storefront-tg lane).
- **LOGIN-URL FORK RESOLVED (cai #2261, RE my #2257):** **231 HOLDS** — persona sweep targets POOLED qa-madrasah-test at `ihsanos.com`, NEVER the silo; my fail-closed guard was RIGHT to refuse irsyad.ihsanos.com; cai is correcting cc-ihsanos #2243 ("you stopped instead of guessing"). **AUDIT ACCEPTED (cai #2253, CAI-RESP-237):** A1/1299 P1-fix-now, A2/1300 P2, A3 NRIC PASS — "DB-grant gaps a UI oracle could never see, proving the DB-oracle rider."
- **CREDS SELF-SERVED:** QA_PERSONA_* passwords are deterministic SYNTHETIC fixtures committed in `scripts/seed-qa-madrasah.ts` (admin/cashier/viewer/parent @qa-madrasah.test, `QaMadrasah<Role>2026!`); personas.ts emails match; the 2026-06-15 live J1 run already authenticated all 4 (4/4) so they're seeded+valid on pooled. No operator export needed. Stagehand ANTHROPIC_API_KEY funded in ihsanos/.env.local (cai #2206).
- **LIVE FULL SWEEP RAN (Task #107, 2026-06-16 ~11:20 SGT):** `tsx scripts/run-persona-sweep.ts` headless vs `https://ihsanos.com`, J1-J8 minus J6 = **23 (role,flow) journeys, 20 PASS / 3 non-pass.** J1/J3/J4/J5/J8 = ALL personas pass (incl parent no-leak, viewer denied on J5, J8 PII floor). 3 non-pass: `cashier:J2`+`cashier:J7` (write controls not seen), `viewer:J7` (expected 404, page rendered).
- **3 NON-PASS CLASSIFIED via evidence-capture (all HARNESS FALSE-FAILS, security posture SOUND):**
  - **`viewer:J7` = HARNESS BUG (FIXED).** Evidence: final URL = `/dashboard`, NOT `/pos` — the app refuses the viewer by **REDIRECT to /dashboard**, not notFound()/404. The viewer never reached POS (correct refusal); `observeRoute` only knew 404/not-found-marker so it mis-scored a redirect-refusal as a leak. **Fixed (TDD):** new pure `isRouteRefused(status, body, finalUrl, requestedUrl)` in route-access.ts treats a redirect off the requested route (final path not the route nor a child of it) as denied; `observeRoute` now calls it. 74/74 persona unit green (+8 new); route-access 30/30.
  - **`cashier:J2` (/counter) + `cashier:J7` (/pos) = interaction-gated writes.** Evidence: both reached (200, not refused). Counter shows only a "Look up" tin-serial field (write actions Issue/Record/Mark render AFTER a tin lookup); POS shows "Open POS Session"→"Open Session" (the entry write; verb "Open" not in mutation lexicon; admin likely had a live session, landing in the register directly). The static landing scan can't see an interaction-gated write. **cai #2235 already deferred mutation-exec to G2** → for G3 the write-PRESENCE check on these pages is the wrong oracle (same lesson as J8 "access").
- **DELIVERED + HANDED OFF:** redirect fix committed **c8189f4** (feat/persona-runner-g3); sweep artifact + classification → **work_output#213 / job#165**; **cai #2267** (ruling request: re-scope cashier J2/J7 to reachability for G3, with the interaction-gating alternative); **cc-ihsanos #2268** (JourneyResult[] handoff + DB-oracle ask: confirm cashier INSERT/UPDATE grant on counter/POS tables & viewer absent). **Post-fix: 21/23 PASS; only 2 non-pass are the gated-write cells, both reachable (no refusal/RLS/PII leak).**
- **cai RULED #2273 (CAI-RESP-239, RE my #2267):** re-scope interaction-gated write cells (cashier J2/J7) to REACHABILITY verdict; write-capability proven at DB-grant + G2, NOT static UI; driver-interaction alternative REJECTED as scope-creep; viewer:J7 fix accepted; "sweep effectively green on the fixed state — good run." Also #2271/#2272: 069 P0 CLOSED on pooled verified live (G1 staff-retain + parent=0 at apply); J5/J7 benign-UX + J8 complementary floors ACCEPTED; cc-ihsanos owns 070 + 1299/1300.
- **IMPLEMENTED (commit d41223d, feat/persona-runner-g3):** ACCESS_MATRIX cashier:J2 + cashier:J7 'write'->'access' (TDD: integrity test flipped RED then GREEN); 74/74 persona unit green; all 11 pre-commit lint checks pass. Re-scored existing capture deterministically: the recorded cashier:J2/J7 error ("expected write controls...") is only reachable when obs.denied=false + PII floor passed, so under 'access' both flip to PASS -> fixed-state sweep = 23/23 (no live re-run needed to know the verdict).
- **NEW FINDING -> escalated cai #2276 (P1 question, requires_response):** read the page components (free) and the SAME interaction-gating makes org_admin:J2/J7 'write' NON-deterministic too. pos/page.tsx L65-84 branches on OPEN-SESSION existence (not role): no session -> <OpenSessionPrompt/> (no static write control); seed-qa-madrasah.ts seeds NO session -> org_admin:J7 passed 'write' in the 03:20 run ONLY on a transient session; a fresh no-session run would FAIL it. counter/page.tsx writes are post-tin-lookup (gated for all roles). RECOMMEND extending 'access' re-scope to org_admin:J2/J7 (RBAC boundary still asserted via viewer denied/readonly + J5 denials + DB-grant). HOLDING the final live re-run until cai rules, so I run ONCE clean on the settled matrix vs post-069 pooled (a run now would nondeterministically flip org_admin:J7 and muddy the artifact).
- **★ STOOD DOWN TO MONITORING (cai CAI-RESP-242 / #2290+#2291, 2026-06-16 14:15):** #2175 persona-runner blocker FORMALLY CLEARED for UAT — G3 was the sole gating item, cross-validated green at both layers. No residual gate is the orchestrator's. **STANDING RE-RUN TRIGGERS (only these two; do NOT keep hot otherwise):** (a) re-run the full 23/23 sweep on ANY RLS/policy migration applied to Irsyad silo OR pooled — watch cc-ihsanos migration traffic + schema changes for this; (b) ONE final full 23/23 sweep immediately pre-demo as a smoke. Acked no-challenge (#2295). Decision in ~24h challenge_window. Open acceptance item (automated sale-commit mutation-exec, createTransactionAction integration test) = CAI-RESP-241, cc-ihsanos scope, non-blocking fast-follow, NOT mine.
- **★ G3 GATE = GREEN (cc-ihsanos #2284, 2026-06-16 13:23):** DB-RLS cross-validation VERDICT **PASS** — the DB oracle corroborates ALL 23/23 browser-grid cells (work_output#214) at the RLS layer, **zero divergence**. cc-ihsanos re-ran every role×flow boundary as txn-wrapped authed RLS sims vs pooled qa-org (5abe01ce): J2 cashier-permit/viewer-deny; J3 parent-denied + batch-write org_admin-only; J4 viewer SELECT-ok/INSERT-deny; J5 admin-only write; J7 cashier-RLS-permit/viewer-deny; J8 NRIC floor (no plaintext any role). qa_findings **#678-687** (10 PASS, sole writer). **The sole failing gate to the S$38k Irsyad UAT is now cleared.** Standing-separate (NOT a grid divergence): pos_orders/pos_order_items INSERT=PUBLIC(true) = finding 1299 (storefront place-order lane, cc-ihsanos-owned REVOKE migration queued).
- **cai RULED #2279 (CAI-RESP-240, RE #2276): YES** — extend the reachability re-scope to org_admin AND generalize to ANY interaction-gated write cell, all roles ("no G3 verdict may depend on transient state"). cc-ihsanos #2277 supplied the DB-oracle backing: cashier write-grant PRESENT + viewer ABSENT on counter/POS.
- **CAI-RESP-240 IMPLEMENTED + FINAL SWEEP GREEN (Task #107 DONE, commit 592601b):** ACCESS_MATRIX org_admin:J2/J3/J5/J7 'write'->'access' (TDD: RED then GREEN; audited every write cell from source — J2 counter post-tin-lookup, J3 keluarga "New batch" off-lexicon + per-batch data-gated Issue, J5 reports/new Create-draft behind Load-tins click, J7 pos register behind opening a session). **J4 (/umum's role-gated static "Issue tin") is the LONE deterministic static-'write' cell** (org_admin/cashier write, viewer readonly). 76/76 persona unit green (route-access 32). **Clean post-069 live full sweep vs pooled https://ihsanos.com = 23/23 deterministic GREEN** (window 11:04-11:07Z); branch pushed (pre-push RLS smoke 7/7, #2192 backup cleared). Final artifact **work_output#214 / job#165**; handed to cc-ihsanos **#2281** (DB-RLS cross-validation + qa_findings, sole writer); acked cai **#2282**. **G3 now awaits ONLY cc-ihsanos's DB-oracle verdict — no code blocker on my half.**
- **branditqr (parallel, operator-gated):** keyless OIDC steps delivered; resumes on operator's slug confirm + Firebase Console Email/Password enable. Stays below persona-runner.

## In Progress (2026-06-13 — Reel Triage v1, CAI-RESP-216/218: full build SHIPPED on branch, reversible-only)

Instagram reel → single concrete action triage. Feature-flagged (`WINGMEN_REEL_TRIAGE_ENABLED`, default OFF), no fork. Three surfaces over one cross-project table `reel_inbox` (Supabase `tscuymavysscrvoberrr`, NOT orch prod): INGEST+DIGEST in the Telegram bot (Mac Mini), WORKER headless on Mac Studio (launchd, fail-closed).

- **Plan:** `docs/superpowers/plans/2026-06-13-reel-triage-v1.md` (12 TDD tasks).
- **Phase:** build complete (all 12 tasks), reversible-only per CAI-RESP-218; merge/apply/deploy gated on window close + operator deps.
- **Build status:** green — 45/45 reel_triage tests pass; cto_bot.py compiles with handlers wired behind the flag.
- **Completed (commits f969b6d→1120ec6):** T1 migration `001_reel_inbox` + psycopg apply script + schema tests; T2-3 config/db/IG-link parsing; T4-5 Meta DYI ZIP parse + idempotent ingest; T6-7 `claude -p` strict-JSON structurer + yt-dlp/ffmpeg fetcher (NO cookies) + whisper transcriber; T8 serial worker loop (claim/full-stderr capture/media cleanup); T9 digest (top5, WIP cap 3, done/discard, auto-discard after 2 digests via `digests_shown`); T10 identity-gated TG ingest + Apply/Discard/Done callbacks, wired into cto_bot in a higher-priority handler group with ApplicationHandlerStop (non-reel text/docs fall through); T11 Friday digest sender + `scripts/run_reel_digest.py` + fail-closed Mac Studio worker plist; T12 zero-IG-creds acceptance guard (green).
- **Binding constraints honored:** identity-gated ingest (Musa's verified TG ID only, silent-ignore otherwise); ZERO IG credentials (grep-guarded); yt-dlp public fetch only, no cookies; media never persists (deleted post-extraction, transcript retained); serial fetch w/ 30-60s sleeps; `priority=impact*confidence/effort_weight`; WIP cap 3; auto-discard after 2 digests; decision-962-safe psycopg apply (never `supabase db push`).
- **Failed/Blocked (operator-gated, NOT mine):** worker not yet on Mac Studio (no `.venv-reel`/toolchain); migration NOT applied (`REEL_INBOX_DB_URL` not provisioned); flag OFF on bot host. All by design — reversible-only window.
- **Next Up:** push branch + open DRAFT PR (reversible-only); then operator go-live steps in the plan's "Operator-Gated Go-Live" section.
- **Questions for CTO (in plan + below):** (1) `REEL_INBOX_DB_URL` creds for project `tscuymavysscrvoberrr`; (2) Mac Studio worker host + toolchain (yt-dlp/ffmpeg/faster-whisper/`claude` CLI) under `.venv-reel`; (3) Friday 09:00 SGT schedule mechanism (orch scheduler vs launchd calendar job) — runner is ready either way.

## Prior In Progress (2026-06-11 — CADENCE-008 A drain worker, execute arm complete behind flag; go-live gated on window + operator)

cc-ihsanos inbox-drain headless worker. Operator authorized the build; orchestrator restarted (pid 91630) to activate the COHERENCE-001 E inserter fix.

- **Plan:** `docs/superpowers/plans/2026-06-11-cadence-008a-ihsanos-drain-worker.md` (7 tasks).
- **Report-only scaffold SHIPPED (commit 3051afe, 17/17 TDD):** `ihsanos_drain/` package — kill_switch (env gate `WINGMEN_IHSANOS_DRAIN_DISABLED`), token_budget + `drain_token_ledger` (apply script dry-run-validated, NOT yet applied to prod), grant predicate, cc-ihsanos poller, substrate work-report writer, single-cycle `main` + `ops/launchd/dev.wingmen.ihsanos-drain.plist` (StartInterval=1800, RunAtLoad=false) + manifest. Never spawns `claude -p`, never mutates source.
- **Grant predicate RATIFIED (cai #2067):** 4 parts as proposed + sha sub-rule (filename mandatory, sha optional-but-binding). Encoded in `ihsanos_drain/grant.py` with allowlist `CLOSED_CHALLENGE_STATES`.
- **Execute arm COMPLETE end-to-end behind `DRAIN_EXECUTE_ENABLED` (TDD-green 45/45; commits 129259b, bbaae8f, b3ee17d, 13b0966):** `ihsanos_drain/runner.py` + `main.py`.
  - `execute_ruling` (DI) gates in order: claude-ok → has-diff (escalated_no_commit / ARCH-021 ghost guard) → migration-refusal → local pre-push CI → **open PR** (escalated_publish_failed / `pr_opened`).
  - Live wrappers mirror ralph_runner (env-whitelist + timeouts): `create_worktree`/`remove_worktree`, `run_claude_in_worktree`, `git_changed_files` (unions committed+staged+**untracked** so new migrations can't slip the gate), `run_local_ci` (CI_STEPS: npm ci/lint/type-check/test, fail-fast), `publish_drain_pr` (reuses canonical `agents/git_publisher`).
  - `run_cycle` drives the execute path per executable ruling when the flag is set; records ledger spend + posts a per-ruling outcome report. Flag defaults **false** → report-only.
- **CI-gate/merge fork RESOLVED — cai #2078 / CAI-RESP-212 = Option B2 (as recommended):** drain pushes branch + opens PR; **REAL GitHub CI is the sole merge gate**; GitHub auto-merge-on-green scoped to `ihsanos-drain-*` branches. Local gates are pre-push filters only, never merge authority. Option A (local-replica + ff-merge) rejected.
- **Validation cycles ran clean (report-only):** #1 (#2069, 09:51 UTC) and #2 (~10:50 UTC) — both polled live, executed 0, open-window rulings (IRSYAD-DEMO-001, TESTER-PERSONAS-IMPL-001) correctly held, kill-switch confirmed. cai's ≥2-clean-cycle gate now met.
- **Execute arm go-live REMAINING gates:**
  1. **CADENCE-008 challenge window** — closes 2026-06-11 14:17 UTC (TIME).
  2. **CAI-RESP-212 condition (a) — OPERATOR click-path (on Musa's list per cai #2079):** enable branch protection on ihsanos `main` requiring all CI checks + configure GitHub auto-merge-on-green scoped to `ihsanos-drain-*`. Auto-merge is inert without it.
  3. **Supervised first run** (operator).
- **Go-live steps (operator-gated, ready):** apply `drain_token_ledger` to prod (dry-run validated), copy plist to `~/Library/LaunchAgents/`, bootstrap, operator-review a report-only cycle, THEN flip `DRAIN_EXECUTE_ENABLED=true`.
- **CAI-RESP-212 condition (e) standing rule:** if canonical GitHub CI is found to gate *less* than the supervised-run baseline (e2e still disabled), PAUSE and escalate — partial canonical CI is acceptable as shared truth, a partial *replica* is not.

## Last Completed (2026-06-11 — BUG-024 Phase 2 build: migration + tests + dry-run)

### BUG-024 Phase 2 — agent identity enforcement (branch feat/bug024-phase2-identity-enforcement, commit 8172325; NOT applied to prod)
- **Authority:** cai #2064 (migration + tests: GO; apply gated on operator distributing per-agent creds). Reported complete to cai in msg #2082 (thread 6df8aaaf).
- **Shipped:** `scripts/apply_bug024_identity_enforcement.py` (dry-run/`--apply`, decision-962 safe) + `tests/migrations/` (ephemeral PG17 cluster fixture + 5 AC tests via SET ROLE). Hardens both `populate_*_provenance` triggers to **SECURITY INVOKER + OVERWRITE** (posted_by_identity = coalesce(jwt agent_id, current_user), caller input ignored), adds RLS INSERT policies (from_agent/decided_by must match resolved identity or identity_allowlist), seeds operator→cai/musa allowlist, VOIDs all pre-existing verified flags.
- **Tested:** 5/5 ACs green on local PG17 substrate (prod-via-pooler unusable for SET ROLE). Dry-run vs prod CLEAN (rolled back): would void 60 agent_messages + 20 strategic_decisions stale flags; existing trigger/policy names confirmed matching.
- **Design finding (INVOKER):** existing triggers were SECURITY DEFINER → current_user=postgres inside them, so stamp would disagree with RLS (which sees real caller). INVOKER makes stamp + enforcement agree; behavior-neutral on legacy shared-key path.
- **SELECT-visibility companion COMPLETE (cai CAI-RESP-213/214; reported green #2089).** Both SELECT policies now in the same script as the INSERT migration — INSERT half must never apply alone (CAI-RESP-213); both ride the single operator gate.
  - **agent_messages SELECT (commit bb0370b, ACs 6-9):** own inbox (to_agent=self) + own sent (from_agent=self) + broadcast. Ratified as-built; cai endorsed the `'broadcast'` literal over their `'all'` guess.
  - **strategic_decisions SELECT (commit e220d9a, AC10):** shared-ledger `USING(true)` per CAI-RESP-214 (b). Own-only overruled — drain grant-check + challenge windows read others' decisions every cycle; own-only would silently starve them. WRITE integrity stays with the INSERT policy.
  - **10/10 ACs green; full dry-run vs prod clean.** Branch local only (not pushed). Apply gated on operator per-agent role provisioning.

### Incident #1994 / BUG-024 — operator-button identity gate (commit c8e8a65)
- **Forensics:** #1980 from_agent='musa' APPROVE proven to be test-suite traffic — `handle_button_callback` has no live wiring; all 22 musa-button rows carry the test-fixture subject "test escalation subject". Severity down-classified. 0 forged real rows, 0 leaks.
- **Fix:** `cc_cai_daemon/telegram_bot.py` `handle_button_callback` now takes `caller_telegram_id` + `operator_telegram_id`. `verified = bool(operator_telegram_id) and str(caller)==str(operator)`. Verified → from_agent='musa', from_agent_verified=true, side-effects applied, is_test inherits source. Unverified → from_agent='substrate', is_test=true, from_agent_verified=false, sub_tag='substrate-button-unverified', NO source mutation. Gate lives in the handler so a future dispatcher can't bypass it.
- Tests `tests/cc_cai_daemon/test_telegram_bot.py` 15/15 (added verified/unverified/no-mutation cases).
- `handle_free_text_reply` annotated: MUST gain the same gate when CADENCE-008 C wires it.
- **Closure pending** operator real-press smoke test once buttons go live.

### SUBSTRATE-COHERENCE-001 E — from_agent inserter migration (commit 1b79a55)
- cai #1990 option (b): automation processes are NOT agents. `from_agent` stays a closed canonical set enforced by the (already-VALIDATED) `agent_messages_from_agent_fkey`. Discovered the FK had been silently rejecting ralph_runner / arch-030-escalation writes all along (0 such rows ever landed — swallowed exceptions).
- `wingmen_orch.py` 10 sites migrated: 4 arch-030 + 4 ralph_runner inserts now post `from_agent='substrate'` with origin in `sub_tag` ('substrate-arch-030-escalation' / 'substrate-ralph-runner'); cap-check read + spawned-CC prompt text updated to match.
- Registered `substrate` in the `agents` table (FK target).
- **Restart required:** wingmen_orch.py is the always-on process — needs `scripts/restart_orch.sh` to take effect (flagged to cai, not auto-restarted).

### SUBSTRATE-COHERENCE-001 D — archived status (commit 30daa2a)
- cai #2001: `strategic_decisions_status_check` expanded to allow 'archived'. Applied to prod via `scripts/apply_archived_status.py` (idempotent psycopg-apply, decision-962 safe).
- `schema.sql` reconciled (status column + CHECK were missing from the strategic_decisions definition — pre-existing drift, related to BUG-036).
- decided_by canon (E from prior session): verified already-applied (all canonical, CHECK present) — not re-run.

## Last Completed (2026-06-11 — SUBSTRATE-COHERENCE-001 B + BUG-035 primitive)

### SUBSTRATE-COHERENCE-001 (cai #1963) — B/C/E/F applied to prod
- **B is_test hygiene:** 22 test rows backfilled; operator-button handler now propagates `is_test` from the source message (was hardcoding false → 21 leaked "test escalation subject" rows); `inbox_sla_violations` + `boot_briefing.inbox_hygiene` exclude `is_test`. Verified 0 leaks.
- **C boot_briefing diet:** 656→141 rows (active_decision 30-day window + pinned set, inbox_sla aggregate, repo_snapshot arm dropped).
- **E decided_by canon:** 93 rows normalized to canonical agent set + CHECK. `from_agent` CHECK DEFERRED — blocked on cai ruling re ralph_runner / arch-030-escalation writers (challenge #1967).
- **F:** repo_snapshot arm removed from boot_briefing (table drop is a separate destructive step, not done).
- **D, G:** gated on Irsyad-green + migration 064.
- Apply scripts: `scripts/apply_{boot_briefing_diet,identity_canon,sla_is_test,boot_briefing_inbox_hygiene_istest}.py` (psycopg-apply, decision-962 safe).

### BUG-035 reconciliation primitive (CAI-RESP-205) — shipped (substrate half)
- **read != reconciled** fix: cross-agent BLOCKING handoffs now have a checked reconciliation state.
- `blocking_tasks` table + `strategic_decisions.unblocks_task_id` + `open_blocking_tasks` view + `boot_briefing.open_blocking_task` arm. Helper `nervous_system/blocking_tasks.py` (create/reconcile/list, 6/6 TDD). `reconciled_at` is an explicit owner close, NOT auto-stamped on ruling-existence.
- Spec/plan: `docs/superpowers/{specs,plans}/2026-06-11-bug035-reconciliation-primitive*`. Apply: `scripts/apply_blocking_tasks_schema.py` + `scripts/apply_boot_briefing_blocking_tasks_arm.py`.
- **Adoption handed to cc-ihsanos** (create at raise, reconcile at consume) — msg #2019.

## Last Completed (2026-04-29 — ORCHESTRATOR-STATUS-001 Option B + SKILLS-SUBSTRATE-001)

### ORCHESTRATOR-STATUS-001 Option B — shipped (PR #11, CAI-RESP-102 AGREED)

Plan: `docs/superpowers/plans/2026-04-28-orchestrator-status-001-option-b-implementation.md` (squash commit `fb15c79`)
Thread: ORCHESTRATOR-STATUS-001 (CAI-RESP-083 design, CAI-RESP-080 R2 review protocol, CAI-RESP-102 AGREED)

**Goal:** Mechanical pr_open → deployed verification chain. cc-cosem's publisher (Option C) sets `bug_reports.status='pr_open'` + `jobs.pr_number/branch_name`; this verifier polls every 5 min, confirms PR merged + commit on origin/main + deploy serving the SHA, then flips to `status='deployed'` + `verified_at`. Bypasses go through `manual_override_reason` (≥20-char CHECK per CAI-PIPELINE-BYPASS-001 AC-1).

**Shape delivered:**
- Migration `supabase/migrations/20260428_orchestrator_status_001_option_b.sql`: 5 bug_reports cols (verified_at / verification_started_at / verification_diagnostic / manual_override_reason / verification_escalated_at) + status CHECK expansion (pr_open / push_failed / pr_failed) + manual_override_reason ≥20-char CHECK + 3 jobs cols (pr_number / branch_name / merged_commit_sha) + boot_briefing rebuild with manual_override_bugs UNION branch + Section 6 DO-block assertion gate. Applied to live DB.
- Worker `nervous_system/deploy_verifier.py` (590 lines): 3-case state machine (CASE 1 no PR / CASE 2 open / CASE 3 merged), Vercel target=production filter (CHALLENGE-2), Firebase degraded mode (ARCH-FIREBASE-DEPLOY-SHA tracks future), dual-window timeouts (30-min deploy-lag from pr.merged_at, 24h PR-open from pr.created_at), verification_escalated_at tombstone (CHALLENGE-4: P1 fires once, no infinite spam), `ORCHESTRATOR_VERIFY_ENABLED` env-flag gate (CHALLENGE-3 default false).
- Wired into `wingmen_orch.py` main loop every 10 polls (~5 min).
- Tests `tests/test_deploy_verifier.py` (30 tests passing): live-DB integration for migration + mocked subprocess/httpx for worker logic + per-bug isolation + tombstone-failure test.
- Backfill `scripts/backfill_option_b_historical_bugs.py`: 5 historical bugs annotated with manual_override_reason. Idempotent. Already executed.

### SKILLS-SUBSTRATE-001 — shipped inline with Option B (CAI-RESP-097)

- `skills/README.md` — two-tier pattern + transclusion model + cross-CC-family domain skills (CAI-RESP-073)
- `skills/bypass-approval-policy.md` — CAI-PIPELINE-BYPASS-001 AC-5 directive
- `skills/inbox-check-protocol.md` — Fix 4 inbox discipline trigger (Mar 2026 ORCHESTRATOR-NOTIFIER-FIX-001-AMEND)
- `docs/governance/inbox-check-directive.md` — canonical text (long-form procedure)

cc-orchestrator owns `skills/` per CAI-AGENTS-002. AC-SKILLS-4 (cross-family submodule mount per CAI-RESP-073 GAP 3) tracked-deferred.

**CAI verification (CAI-RESP-102):** 5 SQL queries against live DB — all PASSED. All 4 CHALLENGEs from CAI-RESP-080 satisfied (assertion gate / production-only filter / soak gate / escalation tombstone).

**Soak window binding:** `ORCHESTRATOR_VERIFY_ENABLED` stays FALSE post-merge. Three-step handoff before flip:
1. Observe one CASE 3 happy-path in production (PR merged + Vercel prod deploy detected + verified_at set)
2. File evidence to CAI-RESP-102 thread
3. CAI ratifies + authorizes flag flip

**cc-cosem boundary:** Her #955 commits Option C publisher merges same-day after my migration applies. Migration applied → she is unblocked.

---

## Last Completed (2026-04-20 — GOVERNANCE-CLEANUP-001 Step 3 launcher multi-repo + dual-identity + Opus 4.7)

### GOVERNANCE-CLEANUP-001 Step 3 — shipped

Plan: `docs/superpowers/plans/2026-04-20-step-3-launcher-multi-repo-identity.md` (commits `797565e` base + `77b6111` delta-v2 + `7be7519` worktree amendment)
Thread: GOVERNANCE-CLEANUP-001 (composes msgs 315 multi-repo scope / 317 auto-identity / 324 Opus 4.7 default; integrates CAI msgs 395+397 deltas)
Commits (Step 3 span, oldest → newest):
- Helper module (Tasks 1–6): `e0e26d6` scaffold → `9a3d6ba` load_family_map → `319b1ac` resolve_base_agent_id → `37c5ae1` import hoist → `60899ec` pick_sub_tag → `cdd8a17` allocate + `fd86540` code-review fixes → `882aab4` scan_overlap_siblings → `eaa086f` CLI + `9b7eaf3` fail-loud fix
- Launcher (Tasks 7–10): `8451603` --repo arg + helper invocation + `bc59dde` empty-agent guard → `73e0b05` header/context/heartbeat dual identity → `942d84d` vercel blockers + exit trap split → `0a85ba5` --model claude-opus-4-7 default with MODEL env override

**Goal:** Replace the hardcoded single-identity launcher with a dual-identity, multi-repo, structurally-drift-resistant one. `CC_AGENT_ID` (sub-tag, e.g. `cc-ihsanos-3`) carries per-session identity for `agent_status` + GUC; `CC_BASE_AGENT_ID` (family, e.g. `cc-ihsanos`) stays on the FK-registered `agents.id` row for `agent_messages.from_agent`. Unrecognized pwd = fail-fast ABORT before claude starts.

**Shape delivered:**
- New helper `scripts/lib/auto_agent_id.py` — pwd → family map loaded at launch from `agents.repo_scope` (data-driven, no hardcoded constant; wingmen- prefix stripped; duplicate-claim raises ValueError). Worktree suffixes stripped via `git rev-parse --show-toplevel` + regex (`orchestrator-LEDGER` / `orchestrator.wt-qurban` both → `orchestrator`).
- `resolve_base_agent_id(pwd, family_map)` — pure, unit-testable; pwd outside `~/wingmen/` raises `UnknownRepoError`.
- `pick_sub_tag` + `allocate_sub_tag_and_register` — scan + pick smallest-free N + UPSERT `agent_status` all in one TX under `pg_try_advisory_xact_lock('cc-agent-id-alloc')` with 10×500ms retry (5s ceiling) and `pg_locks` diagnostic on timeout. Stale/offline rows reclaimable (30-min cutoff).
- `scan_overlap_siblings` — soft-warn when another family instance holds overlapping `scope_repos`; returns `list[tuple[str, int]]` (agent_id, heartbeat_age_s) so the launcher header prints `cc-ihsanos-2 (3s ago)` at a glance.
- CLI `python -m scripts.lib.auto_agent_id --pwd X --repo Y --dsn Z` emits `{"sub_tag", "base", "siblings", "overlap_warnings"}`; fail-loud on bad DSN (`DatabaseError: <type>: <msg>` exit 1) and unknown pwd (`UnknownRepoError` exit 1).
- Launcher `scripts/launch_dangerous_cc.sh` — single-pass argv parser respects `--` boundary (CLAUDE_PASSTHROUGH array); `--repo` flag (space or equals form); .env sourced with `set -a` so DATABASE_URL wins over shell env; dual exports `CC_AGENT_ID` + `CC_BASE_AGENT_ID`; header shows both identities + pwd + overlap warnings.
- `build_launch_context --agent "$CC_BASE_AGENT_ID"` (not sub-tag — per-FAMILY context builder; delta-v2 L3-A1 fix catches silent-empty-inbox regression).
- Exit trap split along identity axis: `agent_status` flip to offline uses sub-tag (psycopg + GUC); `agent_messages` session-digest + `agents.status` update use base; sub-tag carried in subject `[cc-ihsanos-N]` + body `Sub-tag: ...`.
- Heartbeat loop dual-writes: `agents.last_heartbeat` (base, supabase-py) + `agent_status.last_heartbeat` (sub-tag, psycopg with GUC) every 5 min.
- Model default: `--model claude-opus-4-7` hardcoded with `MODEL` env override; `--model "$RESOLVED_MODEL"` appended FIRST so operator `-- --model X` wins via claude's last-wins flag parsing. Current_task stamped with `session-launch model=X repo=Y` for CAI drift observability.
- Self-surgery safe: Task 11 smoke verified the edits don't regress the base-case re-launch (cc-ihsanos-3 itself re-launches cleanly into the new launcher).

**Live verification:**
- 75/77 pytest (`tests/test_auto_agent_id.py` + `tests/test_agent_messages_poll.py`) PASS; 2 failures are the pre-existing `"claude.ai" in text` checks (out-of-scope, pre-date Step 3).
- Smoke from `~/wingmen/orchestrator`: allocator returned `cc-ihsanos-1` with shape `('cc-ihsanos-1', 'working', 'session-launch', ['orchestrator'])` — matches spec exactly.
- Both `--repo foo` and `--repo=foo` forms parse natively via argparse — launcher regression covered.
- `bash -n scripts/launch_dangerous_cc.sh` clean at every task commit.

**Deferred to Step 4 (BUG-024 Phase 1):** sub-identity promotion to first-class FK (`agents.id` rows per sub-tag); current dual-write (`agents.last_heartbeat` via base + `agent_status.last_heartbeat` via sub-tag) bridges the gap until the structural identity capstone lands.

**CAI adversarial review:** pending — review request to be filed at task close.

---

## Next Steps

### Deferred from Step 3.5 (CAI-RESP-053)

- **Step 4 (D1)**: BUG-024 Phase 1 — promote sub-identity (`cc-ihsanos-N`)
  to first-class `agents.id` FK. Collapses the current dual-identity split
  (base in `agents`, sub-tag in `agent_status` under GUC) into a single
  FK-coherent surface. Every new write site between now and Phase 1 is a
  BUG-024 re-introduction risk. Committed-date: TBD after Step 3.5 ships.

- **Step 5 (D2)**: BUG-027 — exit-trap janitor cron. Exit trap doesn't
  survive `kill -9`, so stale `agent_status` rows can linger past the
  `stale_agents` view's 15-min threshold. Cron-based janitor flips rows
  with `last_heartbeat < now() - interval '30 minutes'` to `offline`.
  Committed-date: TBD after Step 4.

---

## Previously Completed (2026-04-20 — GOVERNANCE-CLEANUP-001 Step 2 governance hygiene batch)

### GOVERNANCE-CLEANUP-001 Step 2 — shipped
Plan: `docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md`
Thread: agent_messages `4af8f733-4ba4-48fd-91f0-ce0616b1a70b` (msgs 339 → 380 → 387)
Commits: `eb1746a` (migration + verify + plan + poll.py H6 comment) → `469a79d` (app-code + tests)
Migration: `supabase/migrations/20260420_governance_hygiene_batch.sql` applied via CAI MCP — 7/7 Python verify matrix PASS

**Goal:** Compose six structural fixes so the governance substrate stops depending on discipline to stay clean.

**Shape delivered:**
- `strategic_decisions.challenge_status` gains `'superseded'` enum value
- `strategic_decisions.superseded_by_decision_ref TEXT REFERENCES strategic_decisions(decision_ref) ON DELETE RESTRICT` + partial index
- `strategic_decisions_no_self_supersede_check` CHECK prevents circular lineage (CAI msg 380 A3)
- `agent_messages.skipped_at TIMESTAMPTZ` + partial index — dedicated column for notifier-skipped rows (P3/non-routable)
- CAI-LEDGER-004 re-flipped `overridden → superseded` with lineage FK to `CAI-LEDGER-004-REV01`
- 13 Step 1 announce-noise rows (msgs 360-372) bulk-closed, strict-scoped `from_agent='cai' AND to_agent='cc-ihsanos'`
- `trigger_cai_decision_announce` gains OLD-side guard — suppresses hygiene-flip announce storms when `OLD.announced_by_msg_id IS NOT NULL OR OLD.execution_status = 'implemented'`
- New AFTER UPDATE trigger `trigger_cai_decision_autoclose_announce` — auto-closes linked announce row on `execution_status → 'implemented'`
- Both trigger functions pinned with `SET search_path = public, pg_temp` (CVE-2018-1058 class)
- pg_cron `governance_banned_prefix_purge_24h` (03:15 UTC, 4-NULL criterion) + `agent_status_history_90d_ttl` (04:00 UTC); both wrapped in `DO $$ IF NOT EXISTS` idempotency guards
- `agent_messages_poll.py` — new `_mark_skipped` helper, stamped on None-skip path, `.is_('skipped_at', 'null')` added to poll query so skipped rows exit the hot set
- Banned-prefix regex kept in sync by convention between Python L45 and SQL D.1 (cross-ref comments on both sides); extraction filed as jobs #109 follow-up

**Live verification:** `scripts/verify_governance_hygiene_batch.py` — 7/7 PASS live against applied migration:
- Case 1 superseded enum ✓ · Case 2 FK RESTRICT ✓ · Case 3 skipped_at column ✓ · Case 4 fresh announce fires ✓ · Case 5 hygiene flip suppressed (13-row storm regression) ✓ · Case 6 auto-close on implementation ✓ · Case 7 re-announce prevented ✓

**Semantic note:** the OLD-side guard on `OLD.announced_by_msg_id IS NOT NULL` is a semantic improvement over BUG-025 — a decision filed as `challenge_window` that later flips to `accepted` now fires ONE announce, not two. Strictly better; named here so future archaeologists trace the change intentionally.

**CAI adversarial review:** msg 379 → 380 (0 blockers, H1-H7 self-flagged + A1-A3 adds) → 381 (hardening delta) → 387 (APPLIED via MCP, 12/12 structural + 4/4 behavioral smoke PASS).

---

## Previously Completed (2026-04-20 — ARCH-036 priority column on narrowed agent_messages)

### ARCH-036 — shipped
Plan: `docs/superpowers/plans/2026-04-20-arch-036-priority-column.md`
Spec: `docs/superpowers/specs/2026-04-19-arch-036-priority-column-design.md` (CAI-approved)
Docs (WINGMEN_CONSTRAINTS): commit `971b759` (ihsanos repo — file lives there) — priority rubric P0/P1/P2/P3 added under ARCH-035 Part 8
Code commit: `b77a58c` 4-file atomic (migration + agent_messages_poll.py + build_launch_context.py + tests)
Migration: `supabase/migrations/20260420_arch036_priority_column.sql` applied live via Supabase MCP under Musa's delegation (CAI msg #311) — 246 rows backfilled to P2, 5/5 smoke PASS

**Goal:** Add P0/P1/P2/P3 priority taxonomy to agent_messages so urgent traffic surfaces first and passive FYI doesn't interrupt.

**Shape delivered:**
- `agent_messages.priority TEXT NOT NULL DEFAULT 'P2'` with 4-value CHECK (`P0`/`P1`/`P2`/`P3`)
- Anti-inflation CHECK: `P0` and `P1` priorities require `requires_response=true` (cannot mass-mark FYI as urgent)
- Partial index `idx_agent_messages_priority_unread` on `(priority, created_at)` WHERE `read_at IS NULL` (hot-set sort)
- Backfill: all 246 pre-existing rows defaulted to `P2`
- `agent_messages_poll.py` — sort priority-first, prepend 🔴/🟠/🟡 glyph for P0/P1/P2, suppress P3 from Telegram entirely (two-layer defense alongside ARCH-035 banned-prefix filter)
- `build_launch_context.py` — boot-briefing SELECT + ORDER BY priority; `[P0]`/`[P1]`/`[P2]`/`[P3]` tags rendered for all priorities (briefings show everything; only Telegram suppresses P3)
- 8 new tests in `TestPriorityFormat` class; all green (372 pass / 9 pre-existing fail unchanged)

**Live verification:** Option B simulated harness (Task 6) — direct module invocation against live DB:
- P0 row → `_format_telegram` returns 🔴 prefix ✓
- P1 row → `_format_telegram` returns 🟠 prefix ✓
- P2 row → `_format_telegram` returns 🟡 prefix ✓
- P3 row → `_format_telegram` returns None (suppressed) ✓
- Boot-briefing renders [Pn] tag for all priorities including P3 ✓

Direct dogfood follow-up (Task 8): this session's digest posted as `priority='P3'` to `agent_messages` to verify suppression once Musa cycles launchd. If row lands silently in agent_messages and Musa never gets a Telegram for it, the ARCH-036 P3 suppression rule works as designed.

**Action required:** Musa cycles launchd (`launchctl kickstart -k gui/$(id -u)/dev.wingmen.orchestrator`) to load new poller. Task #76 (parent ARCH-036) ready to close after cycle confirms dogfood suppression.

**Follow-ups still open:** task #97 (ARCH-035 pg_cron purge banned-prefix rows after 24h), #73 (BUG-024 Phase 1 per-agent identity), #77 (ARCH-034 tiered CC Supabase access — gated on BUG-024), #55 (LEDGER spec review).

Next P0: TBD after launchd cycle confirms ARCH-036 dogfood passes.

## Previously Completed (2026-04-19 — ARCH-035 three-channel governance taxonomy)

### ARCH-035 — shipped
Plan: `docs/superpowers/plans/2026-04-19-arch-035-three-channel-taxonomy.md` (commit `6a1da85`)
Spec: `docs/superpowers/specs/2026-04-19-arch-035-three-channel-governance-taxonomy-design.md` (commit `f6b5483`, CAI-approved via CAI-RESP-036 + 042 + 043 + 044)
Docs (WINGMEN_CONSTRAINTS): commit `8a96c6c` + nit fix `58c535b` (ihsanos repo — file lives there)
Code commit: `ccb136a` 7-file atomic (base migration + CAI-RESP-046 hotfix migration + build_launch_context.py + launch_dangerous_cc.sh + agent_messages_poll.py + tests + requirements.txt)
Migrations: `supabase/migrations/20260419_arch035_three_channel_taxonomy.sql` + `20260419_arch035_cai_resp_046_hotfix.sql` (both applied live via Supabase MCP under Musa's delegation default — CAI-RESP-046)

**Shape delivered:**
- `agent_status` table: 1 row/agent, 4-value status CHECK (`idle`/`working`/`blocked`/`offline`), GUC-guarded writes
- `agent_status_history` table: AFTER-trigger append-only snapshot on every INSERT/UPDATE
- `agent_status_identity_violations` table: retained but empty under hotfix (CAI-RESP-046 Deviation 2 — dblink→RAISE NOTICE; violations land in Postgres server logs until BUG-024 Phase 1)
- `stale_agents` view: 15-min heartbeat drift surface
- `agent_messages` CHECK: 8 legal message_type values (`review_request`, `question`, `decision`, `agreed`, `challenge`, `update`, `blocker`, `counter`). CAI-RESP-046 Deviation 1 preserved `counter` (pre-existing in pg_constraint, 0 rows, may represent counter-proposal semantics distinct from `challenge`)
- Banned prefixes rejected by notifier: `^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):` — row left UNREAD as tripwire, 24h purge cron filed as task #97
- Boot briefing: new "World State (N agents)" section between Agent-context and Unread-inbox in `build_launch_context.py`
- Launch protocol: psycopg direct `SELECT set_config('app.current_agent_id', …, true)` + UPSERT at launch; offline UPDATE in EXIT trap. No RPC wrapper (CAI-RESP-043 B1 — RPC structurally defeats the tripwire)

**Live verification (8/8 smoke cases passed, cc-smoketest-live fixture):**
- Launch SQL UPSERT → agent_status row created with status=working, current_task=session-launch ✓
- Identity tripwire → GUC mismatch raises 42501 with "identity mismatch" message ✓
- Boot briefing dry-run → "## World State (1 agents)" section renders correctly ✓
- Banned-prefix `_is_routable` → returns False on `CLAIM:` subject (simulated via direct module invocation against live DB; unit tests cover deployment path — orchestrator restart picks up new code post-push) ✓
- Row left UNREAD + forwarded_to_telegram_at NULL after drop ✓
- Exit SQL UPDATE → status=offline, current_task=NULL ✓
- History table → 2 rows in correct order (working/session-launch → offline/NULL) ✓
- Cleanup → all cc-smoketest-live rows deleted from agent_status + agent_status_history ✓

**Known-degraded until BUG-024 Phase 1:** (1) GUC tripwire relies on launch-script trust — spoofing requires editing `scripts/launch_dangerous_cc.sh` (auditable); (2) `agent_status_identity_violations` table empty until dblink auth or per-agent JWT lands (CAI-RESP-046 Deviation 2). Proper per-agent identity replaces both when BUG-024 ships.

**Follow-ups filed:** task #97 (pg_cron purge banned-prefix rows after 24h). ARCH-036 priority column on narrowed agent_messages (task #76) now unblocked.

**Test baseline:** pytest full suite unchanged — 9 new banned-prefix cases in `test_agent_messages_poll.py` all PASS, no regressions.

Next P0: ARCH-036 (priority column on narrowed agent_messages, unblocked now that ARCH-035 has shipped).

## Previous Completed (2026-04-19 — BUG-025 acceptance-path announce trigger)

### BUG-025 — shipped
Plan: `docs/superpowers/plans/2026-04-19-bug-025-acceptance-path-trigger.md`
Spec: CAI-RESP-040 (B1 + A1 + A2 + concession on simpler announce-all-CAI variant)
Migration: `supabase/migrations/20260419_bug025_acceptance_path_announce.sql` (commit `0893d7a`, applied live via dashboard)

**Behaviour change vs BUG-020 (357a135):**
- Announceable status set widened: `'challenge_window'` → `('challenge_window', 'accepted')`
- Message shape branches on `challenge_status`:
  - `challenge_window` → `message_type='review_request'`, subject `"<ref>: <title> — for review + challenge"`, `requires_response=true` (BUG-020 preserved)
  - `accepted` → `message_type='decision'`, subject `"<ref>: <title>"`, `requires_response=false` (BUG-025 new)
- `OLD.challenge_status='challenge_window'` state-transition guard dropped — `announced_by_msg_id IS NOT NULL` is the universal dedup
- No SIMILAR TO regex on `decision_ref` — `source='claude_ai_session'` is the canonical "from CAI" signal (A1)

**Live verification (4-case matrix per CAI-RESP-040 A2):**
- CAI-TEST-001 (accepted path): announced as `decision` + `requires_response=false`, no challenge suffix → PASS
- CAI-TEST-002 (challenge_window regression): announced as `review_request` + challenge suffix preserved → PASS
- CAI-TEST-003 (bypass_review escape hatch): no announce, no notified_at → PASS
- CAI-TEST-004 (state-transition dedup): insert as challenge_window then UPDATE to accepted yielded exactly 1 announce, `announced_by_msg_id` unchanged → PASS
- All 4 synthetic `BUG-025-VERIFY-NNN` rows hard-deleted from `strategic_decisions` and `agent_messages` post-verify

**Schema NOT NULL discoveries (live testing, not in plan):**
- `strategic_decisions` requires `decision`, `reasoning`, `domain` (no `body` column). Plan's verification scripts assumed a `body` column and used Node `@supabase/supabase-js` which isn't installed in the Python orchestrator — used Python `.venv` + correct field set instead.

**Outcome:** CAI-RESP-* and CAI-* acceptance-path rulings now appear in cc-ihsanos's inbox automatically. No more manual paste-by-Musa for accepted decisions. Closes the third bug in the BUG-019/020/025 governance-comms family.

Next P0: ARCH-035 (agent_status table + channel split per CAI-RESP-036).

## Previous Completed (2026-04-19 — governance comms v1 hardening)

### BUG-020 + BUG-021 — shipped
Plan: `docs/superpowers/plans/2026-04-19-governance-comms-pipeline-hardening.md`
Spec: `docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md`

**Schema migration** (`supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql`, applied live):
- `agent_messages.forwarded_to_telegram_at TIMESTAMPTZ` (BUG-021 — middleware's own stamp column)
- `strategic_decisions.announced_by_msg_id BIGINT REFERENCES agent_messages(id) ON DELETE SET NULL` (BUG-020 — FK dedup guard)
- Partial indexes on NULL subsets of both columns (hot-set optimisation)
- `trigger_cai_decision_announce()` + BEFORE INSERT/UPDATE triggers: fires only for `source='claude_ai_session' AND challenge_status='challenge_window' AND bypass_review=false AND announced_by_msg_id IS NULL`; UPDATE variant guards against state-noise by checking `OLD.challenge_status != 'challenge_window'`
- Per-orphan atomic backfill DO block (notified_at IS NULL filter — preserves historical manual announcements)

**Code changes:**
- `nervous_system/agent_messages_poll.py` (commit `3472771`): `.is_("forwarded_to_telegram_at","null")` added to polling query; `_mark_read` replaced with `_mark_forwarded` on both live + dedup paths; cc-* guard removed (middleware no longer clobbers `read_at`); docstring updated
- `scripts/build_launch_context.py` (commit `d61e8ff`): stamps `forwarded_to_telegram_at` on surfaced inbox rows instead of `read_at`; classified as middleware per plan (forwards inbox digest to Musa via Telegram)

**Live verification:**
- Test msg id=242 (smoke): forwarder stamped `forwarded_to_telegram_at`, left `read_at IS NULL` ✓
- `strategic_decisions` id=263 BUG-020-VERIFY → trigger created `agent_messages` id=247 + set `announced_by_msg_id` ✓
- FK `ON DELETE SET NULL` verified on cleanup
- Orphan sweep: 0 (14 historical rows correctly excluded by `notified_at IS NULL` filter — manually notified 2026-04-18T11:33:57Z)
- pytest full-suite: 355 pass, 7 pre-existing failures (not regressions — verified by git-stash rollback)

**Outcome:** Governance pipeline now end-to-end: CAI writes strategic_decisions → trigger queues review_request → notifier forwards to Musa → cc-ihsanos detects unprocessed mail via `read_at IS NULL` regardless of forwarder state. No more governance blackouts from the 2026-04-18 pattern.

## Previous session (2026-04-18 evening)

### TASK-043 Phase 3 — Baseline verdict: MARGINAL (cc-ihsanos-3, 22:30 SGT)
- 2h baseline complete (PID 55042, 24 samples @ 5min): avg idle 62.6%, avg user 21.4%, avg sys 16.1%, min idle 3.8%, max idle 82.9%
- Verdict **MARGINAL** (62.6% between 40–70% band) — revisit after ARCH-030 cutover reduces load
- Full report: `reports/mac-mini-baseline-2026-04-18-2230.md`
- Posted as agent_messages #235
- REVIEW bucket follow-up (msg #234): 5/6 flagged processes died naturally between Phase 2 and Phase 3; PID 47922 remaining is ChromeRemoteDesktopHost (launchd-managed, legit) — no action required

### TASK-043 Phase 2 — Mac Mini process audit script shipped
- `scripts/audit_mac_mini.py` fully operational: Phase 1 dry-run, Phase 2 SIGTERM kills, Phase 3 CPU baseline
- Fixed `SUPABASE_SERVICE_KEY` env var name (was `SUPABASE_SERVICE_ROLE_KEY`); added `dotenv` auto-load for standalone use
- Phase 2 cleared 5 orphaned pytest workers (test_queue_stall_detector, 4+ days stale, ~180% CPU freed)
- Commit: `b33db53`

### BUG-022 — agent_messages claim/lock pattern (CC-2 prerequisite)
- `claimed_by TEXT` + `claimed_at TIMESTAMPTZ` added to `agent_messages` in orchestrator Supabase
- `idx_agent_messages_claim` index + `agent_message_stale_claims` view (stale = >15 min old with `responded_at IS NULL`)
- Claim pattern: atomic `UPDATE WHERE claimed_by IS NULL RETURNING *` — 0 rows = lost race
- Verified end-to-end via Python: first claim wins, second returns 0 rows, release works
- CC-2 launch now safe: `CC_AGENT_ID=cc-ihsanos-2 ~/wingmen/orchestrator/scripts/launch_dangerous_cc.sh`

### BUG-023 — pytest-timeout enforcement (hung-test orphan prevention)
- `pytest-timeout==2.4.0` added to `requirements.txt`
- `pytest.ini`: `timeout = 60`, `timeout_method = thread` (asyncio-safe)
- `@pytest.mark.timeout(10)` added to `test_recovery_clears_dedup` (the test that spawned the zombies)
- Commit: `aea4ce2` (pushed)

### TASK-044 and ARCH-033 — reviews posted to CAI
- TASK-044: daily 08:00 SGT cron, allowlist seeded, polling self-deprecation, 24h Telegram escalation
- ARCH-033: Tier 2 optimistic-lock checks, pre-commit runner, repo-specific prompts, bypass allowlist

## Last Completed Job (previous)

## Result Summary
Docs-only change: created `README.md` with H1 + appended `<!-- PIPELINE-TEST-001: pipeline marker 2026-04-17 -->` marker. No code paths touched, no restart required. Audit row written to `work_outputs` by orchestrator.

## Completed (Last 5)
- [green] Job #133: ihsanos — [BUG] Test bug report from E2E — please ignore (4m 34s, deploy: https://ihsanos-3zmjnw81t-musaaaaaaas-projects.vercel.app)
- [green] Job #130: ihsanos — [BUG] Test bug report from E2E — please ignore (6m 8s, deploy: https://ihsanos-gcaepx0rs-musaaaaaaas-projects.vercel.app)
- [green] Job #125: cosem-tdu — [BUG] Visual review failed on attendance-home: [mobile] The Attendance Overview sectio (4m 54s, deploy: N/A)
- [green] Job #124: cosem-tdu — [BUG] Visual review failed on attendance-home: [mobile] The Attendance Overview sectio (8m 55s, deploy: N/A)
- [red] Job #124: cosem-tdu — [BUG] Visual review failed on attendance-home: [mobile] The Attendance Overview sectio (6m 37s, deploy: N/A)

##                                 Recent Jobs (auto-tracked)

Last Updated: 2026-05-06 18:08 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #65 | [TASK-028] Paused job Telegram escalation — no more silent deaths | green | N/A |
| #65 | [TASK-028] Paused job Telegram escalation — tiered alerts with dedup | green | N/A |
| #64 | [TASK-027] Pre-flight dirty-tree check before Claude Code runs | green | N/A |
| #63 | [TASK-026] Decision auto-flip on job completion — close the state-tracking gap | green | N/A |

---

## Recent Jobs (auto-tracked)

Last Updated: 2026-05-06 18:08 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #93 | PIPELINE-TEST-001: Add test marker comment to README.md | red | N/A |
| #85 | [BUG-018] strategic_decisions_poll queues jobs for already-shipped decisions — a | red | N/A |
| #85 | [BUG-018] strategic_decisions_poll shipped-decision filter — evidence_commit_sha IS NULL + challenge_status != 'implemented' | green | N/A |
| #90 | [SMOKE-001] BUG-019 worktree isolation smoke test — append a comment to STATUS.m | red | N/A |
| #84 | [BUG-013] qa_findings.created_at migration — column + index added, bridge unblocked | green | N/A |
| #83 | [BUG-016] Safe-restart procedure — launchctl kickstart helper + runbook; nohup forbidden | green | N/A |
| #82 | [BUG-015] Graceful shutdown asyncio cleanup — cancel pending tasks before loop close | green | N/A |
| #79 | [BUG-012] Gate 6 Haiku empty-JSON fix — ANTHROPIC_API_KEY guard + fail-loud | green | N/A |
| #71 | Queue stall detector — alert CTO on 30min+ queued jobs with dedup | green | N/A |
| #70 | [TASK-033] Zombie running-row cleanup on orchestrator startup | green | N/A |
## Checkpoint 2026-06-22 (cc-orchestrator session, mid-flight lull)
**Merchant storefront** TESTABLE: customer flow live+public (ihsanos.com/shop/dookana-demo — variants+add-ons+re-price), clickable Mini App preview public, merchant editor via magic-link/TG-after-bridge. Money-hole (client-trusted modifier prices) CLOSED on prod (PR#55, reviewed). 075/077 bridge apply at ~17:47Z window = merchant-onboard.
**cosem-adcda**: dashboard ihsanification (5 slices) MERGED; trainer-arabic-trap fixed (PR#144); PWA-offline F2/F3/F5 staged + F1 emulator-proof gating #145; permissions overhaul design approved + 2 LIVE gaps (SEV-1 admin self-escalation, SEV-3 trainee-ID-image PII) fast-tracking under review; geofence/F4 parked (unconfirmed).
**079** (post_journal auth.uid silo): ESCALATED to operator — expedited+granted but no goumlyne-silo-credentialed executor (mirror-restart orphaned the irsyad lane).
**wingmen.dev**: full Vercel cutover chosen (retain Google MX), Vercel domains attached, gated on operator STYLE PICK.
**cai**: re-engaged (was idle/bloated->/clear); governing 307(Vercel platform std)+308(self-audit)+permissions-security+079; assigned me the consolidated substrate-as-product roadmap (non-urgent).
**Self-audit findings** banked: identity/authorship-verification, stale-views, dups, zombies, blocking-dialogs, governance-node-idle, restart-orphaned-creds.

## Checkpoint 2026-07-22 (Nazim console, post-/clear autonomous run)
**Fable substrate fixes LANDED** on `feat/operator-telegram-bridge`: 4 safe fixes (repo_context watchdog LIVE, secret redactor, operator_log fail-closed, cai-stalled page) — `bf6a70e`; watchdog false-positive fixed (psycopg not REST) `c2407ab`. **CAI-RESP-511**: migration 031 (anon write/TRUNCATE lockdown, formalizes cai's live fixes + default-priv hardening) APPLIED + dual-ledger + `scripts/rls_grant_lint.py` — `35368a0`. repo_context unfrozen (was 13d stale) + auto-watchdog every 15min.
**Routed to cai** (bus 10718): mig 032 exec-grant for 76-table anon-write REVOKE (latent, RLS-gated) + `organizations` LIVE anon-read leak + orch_lease #5 TTL review.
**cosem port**: design+scaffold workflow running (wf_1b5af3b4); full port + tests to follow (synthetic; real-data residency-gated). Design at reports/cosem-platform-unified-port-design-20260722.md.
**Owed**: shipforge(11)+storefront(8) fable branches → hub to ship; new-hire wiring pass (operator GO); cred rotation.

## Checkpoint 2026-07-25 (Nazim console — cc-irsyad stood up, op#7015)
**cc-irsyad LIVE (supervised).** The irsyad client (Gazzabyte/Elly) now has a dedicated agent instead of queueing behind the hub. Identity `cc-irsyad` (empty `cc-support` shell retired into it), lane `irsyad` on the **Mini**, worktree `~/wingmen/projects/ihsanos-irsyad` (branch `lane/irsyad`), `.env.local` pinned to the **goumlyne silo** so residency holds by construction. Charter: `reports/cc-irsyad-charter-20260725.md`.
**Trust is staged IN CODE, not by prompt:** `scripts/irsyad_reply.sh` gates every reply on `bot_channels.group_routing->>'agent_phase'` for `gazzabyte-irsyad` — `drill` → `supervised` (draft to Nazim) → `direct`; unknown phase fails CLOSED. Phase now = **supervised**; **the hub still owns the live client thread** until an explicit cutover (flip to `agent-session`/`inject_target=irsyad`, move polling to Mini `nazim-ingest`, `enabled=false`).
**Drill:** passed — refused both traps (prod-deploy a money feature; raw DELETE of live tabung rows), and settled the S/No question against the hub's version with file:line + silo evidence (single continuous `seq` assigned pre-pagination; real defect = grid header not `fixed`, so page 2+ of 354-tin reports loses its heading). Header fix greenlit in-lane (no merge/deploy).
**INCIDENT + FIX:** the drill was seeded into shared `operator_messages`; the hub read it as real client traffic and answered the LIVE Gazzabyte group (retracted 6 min later, honestly). Structural fix: `scripts/lane_drill_seed.py` — drills are `agent_messages` to the lane agent only (`is_test=true`, DRILL-marked), plus `--announce` to the hub before seeding; `_LANE_OWNED_TAGS` exclusion added in `operator_log`. Hub adopted provenance-verify-before-client-send.
**Fleet models (op#7028):** hub + cai switched to **claude-opus-5 in place** (no context loss); `boot_orch.sh`/`boot_cai.sh` now env-driven (`ORCH_MODEL`/`CAI_MODEL`) so a restart can't revert. `scripts/reset_cai.sh` added (mirrors reset_orch); cai reset from the Mini at its own request off `cai-handoff-NOW.md`. Hub is at 100% context — reset owed.
