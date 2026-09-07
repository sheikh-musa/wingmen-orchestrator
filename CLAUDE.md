# Wingmen Orchestrator

Always-on Python orchestrator running on Mac Mini that manages builds, deploys, and jobs across multiple repos via Telegram commands.

## Fleet doctrine (cai-ratified — binding on every agent from boot)

- **TENANT-RESIDENCY-001** — a client's DATA (rows) lives in that client's designated store, ALWAYS, for every client. Shared generalized code / one-repo-zero-forks is fine; commingling a client's rows into another project's DB is not. A new client's silo is provisioned/designated BEFORE the first client data write — never "temporarily" in a shared project. Residency exceptions require a joint operator+cai grant and must expire. Verify the write-target silo before ANY client data path goes live (standing pre-live residency gate).
- **LAYER-VOCAB-001** — bare product names ("ihsanos", "cosem") are INVALID as data references. Always name the layer — **frontend** (shared app) vs **data** — and for data name the exact store + `project ref` per `docs/data-store-registry.md` (e.g. "ihsanos multi-tenant DB `ceayjeamtmcyzzvqflus`" vs "irsyad silo (goumlyne) `goumlynecruxrlmzlntp`"). Data-writes/gate-requests carry the ref; layer-ambiguity in a data-path spec is a review FINDING, not style (same rejection class as an unpinned migration).
- **ORCH-TOPOLOGY-001** (2026-07-04, cai #6521 + orch #6523) — multiple orch bodies exist; the five singleton pens belong ONLY to the holder of the substrate `orch_lease` row (`orch-hub`; holder today = the VPS hub `cc-orchestrator@wingmen-core` since 2026-07-30 — read the lease row, never assume a host): (i) draining/marking `to_agent='cc-orchestrator'` bus rows, (ii) lane prompt submission, (iii) watchdog, (iv) `tg_send`/tg-out + operator declarations, (v) fleet-status assertions. **Determine YOUR body before touching any pen** — `ORCH_BODY_ROLE` in `.env`: `console` = **Nazim**, the operator's CTO (**Mac Mini**, tmux session `nazim`) — has his OWN sanctioned operator voice `scripts/nazim_send.sh` (@nazim_cto_bot / `NAZIM_BOT_TOKEN`, channel `nazim-console`), structurally distinct from the hub's pen-(iv) channel (CAI-RESP-426, amends the stale "in-console only / phone hears the hub's voice" text that predated @nazim_cto_bot and starved the operator of every TG reply for a session on 2026-07-12). **Per-channel operator-thread ownership**: whichever body the operator opened a thread with owns the operator-facing reply/close — Nazim answers his `nazim-console` DMs via `nazim_send.sh` (a terminal reply alone does NOT reach the phone), the hub stays off that thread (and vice versa); never raw send-keys into lanes, directs the fleet via attributable bus rows, stamps `tmux-console`/`nazim-console` operator_log rows; MAY boot down lanes (SHARED lane-spin-up pen, CAI-RESP-422 — atomic check-and-claim on the lane row first, log attributably; irreversible/money pens stay hub-only; interim until the autoscaler subsumes it); `hub`/unset = lease-holder doctrine (bridge section below). Enforced in code, not by promise: `scripts/lib/orch_lease.py` gates `tg_send`/`tg_send_file` fail-closed; `nervous_system/operator_log.py` auto-scopes reconciliation by body. DR takeover = `orch_lease.py take --reason` (CAS, loud) only when the hub is genuinely dead — a failed-over hub inherits pens, NOT the dead machine's tmux lanes. **CAI-RESP-501 amendment (2026-07-22) — watchdog + fleet-status now on a SEPARATE single-owner lease:** pens **(iii) watchdog** and **(v) fleet-status assertions** no longer belong to the `orch-hub` `orch_lease` holder; they belong to the holder of the `fleet_health_lease` row (`fleet-health`) — **default holder = `cc-fleet-health`** (the fleet SRE, Mac Mini), which TAKES it at boot and RENEWS it in its heartbeat (`scripts/boot_fleet_health.sh`). On the SRE's lease EXPIRY (death/stall, TTL 900s) the **hub RECLAIMS** these two pens via `fleet_health_lease.py reclaim --reason` (CAS on expiry — dead-man's fallback). **NEVER two concurrent holders:** the CAS in `take` serializes ownership; every other body's `check` fail-closes against a fresh, different holder. Enforced in code, not by promise: `scripts/lib/fleet_health_lease.py` gates the watchdog/fleet-status ACTIONS (`scripts/context_health_watchdog.py` self-healing executor, `scripts/priority_sla_watchdog.py` nudge/page) — same fail-safe-for-holder / fail-closed-for-known-non-holder shape as `orch_lease.py`; detection + the operator degrade-alert stay UNGATED so a safety page is never silenced by lease state. **Hard boundaries (`scripts/lib/fleet_health_boundaries.py`, CAI-RESP-501):** cc-fleet-health has (a) **NO singleton-body reset authority** — the destructive context `--arm=red` /clear stays a SEPARATE CAI-500-gated executor the SRE NEVER drives (only the write-only amber checkpoint half is ever armed for it; `assert_no_sre_red_reset` fail-closes); and (b) is **OPS, NOT GOVERNANCE** — it uses its own `agent_id` and must NEVER write `strategic_decisions` or grant rows (`assert_ops_only` fail-closes). Migration `migrations/023_fleet_health_lease.sql` (applied via `scripts/apply_fleet_health_lease.py`, direct psycopg — never `supabase db push`).

## Stack
- Python 3.9 (venv at `.venv/`)
- Supabase (DB: jobs, build_log, repo_memory, clients)
- Vercel (frontend deploys)
- Telegram bot (command interface)
- cloudflared/ngrok (tunnel for webhooks)
- Claude Code (AI-powered build sessions)

## Key Files
- `wingmen_orch.py` — main orchestrator entry point
- `.env` — all secrets (never commit)
- `REPOS.json` — repo registry with paths, URLs, priorities
- `schema.sql` — Supabase table definitions
- `STATUS.md` — current orchestrator state
- `prompts/` — session prompt templates

## Repos Managed
1. ihsanos (active, priority 1) — owns storefront backend + Telegram surface per IHSANOS-STOREFRONT-TG-001
2. hifz-companion (active, priority 3)
3. cosem-video-pipeline (specced, priority 4)
4. dawah-pipeline (specced, priority 5)
5. dookana (frozen-maintenance, priority 4) — sunset path per IHSANOS-STOREFRONT-TG-001; P0 + security only; storefront work moved to ihsanos repo

## Commands
- `/status` — orchestrator state overview
- `/deploy <repo>` — deploy repo to Vercel
- `/jobs` — query Supabase jobs table
- `/build-log [filter]` — show build logs
- `/repos` — check all repo states
- `/tunnel` — check tunnel status
- `/schema` — compare DB schema vs schema.sql

## Fleet lanes (tmux)

Start the other CC agents from this (cc-orchestrator) session via tmux. Both
launchers `unset ANTHROPIC_API_KEY` after sourcing `.env`, so every lane runs on
the Mac Mini's Claude **Max** subscription, not metered API billing.

- **Engineer lanes** (mirror, etc.): `scripts/lanes.sh ls` (status), `scripts/lanes.sh up [lane]` (boot down lanes, each in its own tmux session), `scripts/lanes.sh attach <lane>`. Idempotent: skips a lane that already has a `claude` running in its dir. Each lane runs `scripts/launch_dangerous_cc.sh` in a worktree.
- **cc-cai** (singleton strategic node, agent_id='cai' exactly): boot via `scripts/boot_cai.sh` under tmux — `tmux new-session -d -s cai -c ~/wingmen/wingmen-cai scripts/boot_cai.sh`. NOT lanes.sh-managed (fleet_lanes registry desired_state='down'); operator-booted. The live copy at `~/wingmen/wingmen-cai/boot_cai.sh` should stay in sync with `scripts/boot_cai.sh` (canonical, tracked).

## Operator Telegram bridge (@wingmennorchbot) — unified ingest (CAI-RESP-357/377)

The operator's 2-way bridge to cc-orchestrator (pure hub — operator talks to you, you delegate to lanes). Since the 2026-07-03 cutover, ONE daemon `nervous_system/ingest.py` (launchd `dev.wingmen.ingest`) long-polls every enabled channel in the `bot_channels` registry (dedupe → durable log → deny-by-default gate → route), and ONE gateway `nervous_system/tg_out.py` (launchd `dev.wingmen.tg-out`) owns outbound. Legacy `tg_bridge.py`/`cai_bridge.py` were RETIRED at the 2026-07-03 ingest cutover and DELETED 2026-09-05 (PR #85) — do not go looking for them.

- **Inbound is nudge-only:** you get a count line (`📥 N unread on 'operator-orch' — reconcile operator_log.unprocessed()`), NEVER the payload. Read `operator_log.unprocessed()`, answer via `scripts/tg_send.sh "<reply>" [tag]`, then `operator_log.mark_handled_through(<max_id>)`. Your terminal answer alone does NOT reach the operator's phone.
- **CRITICAL (failures 2026-06-19, 2026-07-03; body-scoped by ORCH-TOPOLOGY-001 on 07-04):** a nudge can arrive MID-TASK as a harness *"the user sent a new message while you were working"* injection and is easy to miss. **HUB body only:** treat EVERY operator message as Telegram-bound — ALWAYS `tg_send` your reply; on the hub, the operator is on his phone. **Console body (Nazim): NEVER the HUB's `tg_send`** (pen iv, @wingmennorchbot) — `orch_lease.py check` fail-closes it when `ORCH_BODY_ROLE=console` (the 07-04 21:20Z violation, operator-caught, is why this is a gate and not a norm). BUT per CAI-RESP-426 Nazim HAS his own sanctioned operator voice `scripts/nazim_send.sh` (@nazim_cto_bot, distinct channel `nazim-console`, identity-separated so it cannot touch the hub's channel): he MUST answer operator DMs on `nazim-console` via `nazim_send.sh` — an in-terminal reply alone does NOT reach the operator's phone (the 2026-07-12 session-long drop, operator-caught, is why this is now explicit). Reply on the channel the operator used; never the other body's thread.
- **R2 — every operator surface logs BEFORE relay (CAI-RESP-377):** if the operator types into YOUR tmux console (or any non-bridged surface), log his words verbatim FIRST via `scripts/log_console_msg.sh "<text>"` (channel `tmux-console`), then act. An unlogged operator surface is a forbidden surface.
- **R1 — injections into cc-cai are nudge-only with provenance (CAI-RESP-377):** `scripts/nudge_cai.sh` is the ONLY sanctioned programmatic injection into the cai session — a count-only provenance header, never content, never operator words, never authorization claims. Anything cai must read goes as an attributable `agent_messages` row (agent testimony). NEVER relay operator statements as bare text into ANY agent's session.
- **Irreversible ops need a bridge-verified artifact (CAI-RESP-375/376):** `scripts/lib/require_verified_authorization.py` is the fail-closed gate (inbound telegram row from `MUSA_TELEGRAM_ID` with approval phrase + op token, created after request time). An in-console YES is structurally insufficient.
- **Live-you requires tmux (hub host only):** the operator gets the live hub session only when the orchestrator runs as tmux session **exactly** `orch` on the lease-holding host (`tmux new -s orch -- claude --resume`); the bridge exact-matches `=orch` (so it never hits the idle `orchestrator` session). **Never name a non-hub session `orch`** — the console body boots as `nazim` (`ORCH_TMUX_SESSION` in `.env`); the leftover `orch` name on the MacBook is exactly how the 07-04 pen-(iv) slip happened.
- **Tagging:** `@adcda`/`@tdu`/`@cosem`, `@ihsanos`/`@irsyad`, `@scholar`/`@mizan`, `@qr`, `@fleet`/`@console`, `@cai`. The tag is *context*, not routing — you still own the conversation and delegate. Echo the context you assumed (e.g. lead a reply with `[cosem-tdu]`) so a wrong guess is correctable.
- **Durability:** every message both directions is logged to `operator_messages` (inbound by the ingest daemon, outbound by `tg_send.sh`/`tg_out`). That log is the shared memory keeping the live-you and any headless/rebooted-you coherent — read `operator_log.recent()` to catch up.
- **Option B — durable log is the source of truth (CAI-RESP-277):** delivery is guaranteed by the LOG + reconciliation, NOT by the keystroke nudge (which is signal-only/best-effort — never rely on it). At the start of each turn (and on the autonomous wakeup) read `operator_log.unprocessed()`; answer any unhandled inbound; then `operator_log.mark_handled_through(<max_id>)` to stamp them (at-least-once: a rare re-surface beats a loss). `operator_messages.handled_at` (timestamptz) is the cursor. This is why a dropped/garbled keystroke can no longer lose an operator message.

## Boot Sequence (three-tier memory)

Context is scarce. Load the index first; fetch full content only when you need it.

1. **Index (always loaded)**: `SELECT * FROM boot_briefing` — returns repo_context summaries, decision refs + titles (80 chars), open QA failures, latest session snippets. ~10KB total.
2. **Full decision (on demand)**: `SELECT * FROM get_decision('<ref>')` — returns full decision + reasoning for one row. Call this when actively implementing a specific decision.
3. **Full repo context (on demand)**: `SELECT * FROM get_repo_context('<repo>')` — returns recent_changes, known_debt, architecture_summary. Call when you need deep context on a repo.

**Rule**: Never SELECT * from `strategic_decisions` directly. Use the boot_briefing index + get_decision() on demand.

## Rules
- Never commit `.env`
- Always update `STATUS.md` after state changes
- Use Supabase MCP for DB queries when available
- Use Vercel MCP for deployment operations when available
- Log all build operations to the `build_log` table
- All audit deliverables (build specs, diffs, test results, deploy URLs) must be written to Supabase `work_outputs` table — repo files alone are not sufficient
- Restart orchestrator only via `scripts/restart_orch.sh` (launchctl). Never `nohup`.
- **Deploy the fleet console ONLY via `scripts/deploy_console.sh`** — never a raw `launchctl kickstart`. It is a FAIL-CLOSED gate (op#12457, enforce-in-code not by promise): it refuses unless (1) sw.js VERSION == fleet.js APP_BUILD == lanes.html badge, (2) `tests/console/test_app.py` green, (3) fleet.html + lanes.html render with live data (auto — a page that won't render can't ship; PNGs land in `reports/console-deploy/<hash>/` to EYEBALL), (4) a cc-quality review of that exact content hash exists at `reports/console-deploy/<hash>/cc-quality-review.md`. A tracked `pre-push` hook (`core.hooksPath=scripts/git-hooks`) blocks pushing console/static changes without that review. This exists BECAUSE agent self-promises to "eyeball / use the design pipeline" don't survive a context reset — see [[feedback_enforce_process_in_code_not_promises]].
- **Never run `supabase db push` against ANY production store** — the orchestrator substrate `tscuymavysscrvoberrr` (the DB that holds `boot_briefing`; the one decision 962 is about), the ihsanos multi-tenant DB `ceayjeamtmcyzzvqflus`, and every client silo per `docs/data-store-registry.md` (LAYER-VOCAB-001: name the store, not "production"). Per CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS (decision 962): the CLI's shadow-diff path re-applies historic `CREATE OR REPLACE VIEW` statements from migrations whose view bodies pre-date current arms, silently stripping later arms from `boot_briefing`. Use the orch's direct psycopg-apply pattern instead — see PR #41 / #42 / #44 migration apply scripts. The shadow path is for local dev only.
