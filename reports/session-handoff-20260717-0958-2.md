# Orchestrator handoff — 2026-07-17 ~01:32Z (Nazim-directed pre-reset checkpoint, ~82% ctx)

Fresh handoff written at Nazim's operator-directed checkpoint (do NOT clobber the 0958 handoff — this is the ‑2). Everything below is live state so rebooted-me loses nothing. Standing doctrine unchanged: reconcile bus + operator_log every turn AND before idle; money/PII/residency/audit stay cai/operator-gated; lanes/subagents build authored-unapplied, hub reviews+gates+applies/deploys; guarded `--expect-ref` migration apply, never `supabase db push`; **verify every subagent's tests/claims MYSELF** (a subagent misreported "27 passed"/20-failed this session — trust nothing unverified); drive governance gates FAST not around them (AI-velocity, see memory `ai-velocity-dont-overpad`).

## ⚡ JOB ONE (fresh-me): RELAY TO ELLY — GREEN-LIT, DO IT FIRST THING ON BOOT
**delete-report-before-signing is LIVE + fully verified. Nazim green-lit the client relay via bus #9341.** I was holding the relay only until his prod confirm; that confirm arrived. I did NOT send it because this checkpoint said "no new work."
- **Send via** `scripts/irsyad_support_send.sh` (the Gazzabyte/irsyad client group; NOT tg_send.sh). Standing grant op#4406 — relay directly, promptly, don't wait for operator.
- **What to relay:** delete-a-report-before-signing is now live — you can delete a weekly report while it's an unsigned draft (a reason is required); once it's signed it stays locked/immutable as before (and a report that was ever signed, even if reopened to draft, can't be deleted). This is #1 of her 3 design items → **delivered today, ahead of the "today/tomorrow" dated scope**.
- **Basis it's safe to claim live:** prod deploy = **f623979** (the #169 merge) READY+healthy (my poll + Nazim provenance); migration **107 applied to ceayj** (guarded `--expect-ref`), backfill latched exactly the 1 existing signed report (**Nazim cross-checked on ceayj: total=1, was_ever_signed=1, 0 mis-latch**); 8 tests incl. the named reopened-once-signed laundering test + monotonic-trigger test (I re-ran, pass); cai-blessed CAI-RESP-465 + operator-signed-off op#4879.
- **Then update her dated scopes:** delete = DONE. Tabung Fajr still needs her Fajr-model answer. **GIRO prototype = this week (synthetic-first)** — note the earlier velocity push (2026-07-16): she rightly wants days/hours not weeks; recompressed. Don't re-pad.

## Inbox state (as of handoff)
- `operator_log.unprocessed()` = **0** (all client/operator messages handled through 4887).
- Unread bus to cc-orchestrator = **1**: **#9341 (Nazim, LEFT UNREAD ON PURPOSE)** = the delete-before-sign green-light. Reconcile will surface it → confirms JOB ONE. Mark it read after relaying.

## Task board (13 tasks; #5 GIRO is the ghost-placeholder per Nazim — treat as design-first-pending, below)
SHIPPED-TO-PROD TODAY (all verified): #4 class-completion two-column reformat; #6 student-name lookup (Counter); invoicing AR-gaps Phase 1 (COSEM, a487e6a); cosem-adcda trainee search EN/AR (5dc1fa2, straight-to-live per op#4844).

GATED / IN-FLIGHT:
- **delete-report-before-sign (task#6)** — LIVE (f623979 + migration 107 on ceayj). ONLY remaining = the client relay (JOB ONE). Then mark task#6 completed.
- **exec-reliability-layer MVP (task#11, op#4711 / CAI-RESP-464)** — built, deep-reviewed SOLID, RLS-hardened (claim-steal closed), committed `feat/exec-reliability-layer` (051b5b1, pushed). **Migration APPLIED to substrate** (`scripts/apply_exec_reliability_layer.py --expect-ref tscuymavysscrvoberrr`) — DORMANT, grants-nothing. EXEC-4 double-verified (mine + Nazim's live has_table_privilege: exec_runner SELECT-only on strategic_decisions, no-INSERT on exec_work_items). **REMAINING = STEP-3 GO-LIVE = deliberate WATCHED first-consumer relay test, Nazim's timing (he pings to run it TOGETHER — do NOT wire launchd/start runner solo).** Provision a LOGIN role inheriting exec_runner (migration role is NOLOGIN) + set app.current_agent_id; wire poller+runner+safety-net; seed 1 synthetic granted decision + named relay artifact; watch grant→enqueue→claim→deliver→dedup→bounce; return to cai WITH PROOF (CAI-461). Not urgent.
- **GIRO / bank-statement upload (task#5, Track B)** — Nazim: the GIRO line in the box is a GHOST PLACEHOLDER, ignore as active. Real state: design-first only; MONEY+audit-critical, end-Sept client deadline; RESIDENCY CRUX unresolved (registry says irsyad silo goumlyne `goumlynecruxrlmzlntp`, but live tabung/donations run on ceayj `ceayjeamtmcyzzvqflus` — verify before siting bank-statement PII; cai flagged this is a residency+money-rails review, NOT a quick bless). Client promised "GIRO prototype this week (synthetic-first)". Scope doc: reports/irsyad-design-scopes-20260717.md.
- **Tabung Fajr (task#4)** — design-first; BLOCKED on Elly's inventory-model answer (per class / per student / single issued-vs-returned stock list; how Fajr tins are issued). Non-money → build once she answers.
- **#5 automated summaries (task#2)** — BLOCKED on Elly's format example.
- **Alderei white-label PWA Phase-2 (task#8)** — scope+plan+ping-Nazim BEFORE building; AFTER invoicing Phase-1 (done). Thin branded PWA on shared invoicing backend (ceayj COSEM org), lightly-interactive, lightweight auth, zero create-org, synthetic-first. COSEM real-data HELD (RLS-isolation verify + cai-ratify CAI-460).
- **cosem-adcda orbats-as-PDF (task#13)** — BLOCKED: operator sending PDFs "in a bit". Display previous-batch orbats as PDFs. Straight-to-live per op#4844.
- **cai deliverables (task#9)** — owe cai: CAI-461/462 MVP drafts (exec-layer IS the 461 build → returns to cai with proof at go-live) + the 458 closing proof.
- **storefront shop-synthtest investigation (task#10)** — Nazim's condition: shop-synthtest is RED (m-products viewer "+ New product" e2e) — UNRELATED to invoicing/tabung (merged past it twice per precedent 9129). Investigate flaky vs real storefront perms/visibility regression; own track, not blocking.

## Awaiting operator (non-task)
- **Email:** ihsanos.com has NO MX; wingmen.dev already on Google Workspace. Recommended: add ihsanos.com as a Google domain ALIAS (free) + I wire MX/SPF on Vercel. Operator to decide + grant Google Admin access. musa@ihsanos.com won't receive mail until then.
- **Cloudflare bill:** neither domain uses Cloudflare (both Vercel DNS). Only fleet use = a cloudflared TUNNEL token, likely VESTIGIAL (ingest now long-polls). Told operator DON'T pay blind — get me dashboard access to identify/cancel. He said "let me get back to you."

## Infra / gotchas
- Migration apply: ceayj = `IHSANOS_PROD_DATABASE_URL` (SG); substrate/bus = `DATABASE_URL` (`tscuymavysscrvoberrr`, Sydney); goumlyne = `GOUMLYNE_DATABASE_URL`. Guarded apply = verify ref-in-DSN (fail-closed) → apply → pre/post verify.
- **Lint-baseline cascade (ihsanos):** adding a column triggers `check-schema-drift --full-scan` → don't add per-file ignore COMMENTS to unrelated modules (line-shifts cascade-break pagination + action-error baselines). Use the LINE-INDEPENDENT `schema-drift-baseline.json` (`file:table.column` entries) for unrelated modules; inline comments only in the feature file. Applied on invoicing + delete-before-sign.
- ihsanos CI: `lint-and-typecheck` RED = pre-existing debt (place-order/storefront); `shop-synthtest` RED = unrelated storefront (task#10). Verify your change's own checks (unit/tabung-correctness/tabung-synthtest/irsyad-frs) green.
- No `gh` CLI on Studio hub → PRs via GitHub REST + GH_TOKEN. cosem deploys GATED (feat→PR→CI→staging; op#4844 authorized straight-to-live for cosem-adcda, but hub keeps review+eyeball).
- psycopg (v3) not psycopg2; substrate DB has transient Sydney-pooler timeouts — use connect_timeout + retry loop.
- Fleet: hub (Studio, ORCH_BODY_ROLE=hub, lease held), cai (fresh post-reset, idle, awaiting my CAI-461/462 + 458 proof; cai composer wedges recurrently = Studio-local, FLAG never inject), shipforge, support. Nazim = orch-console on the Mini.
