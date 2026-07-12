# Wingmen Orchestrator

Always-on Python orchestrator running on Mac Mini that manages builds, deploys, and jobs across multiple repos via Telegram commands.

## Fleet doctrine (cai-ratified — binding on every agent from boot)

- **TENANT-RESIDENCY-001** — a client's DATA (rows) lives in that client's designated store, ALWAYS, for every client. Shared generalized code / one-repo-zero-forks is fine; commingling a client's rows into another project's DB is not. A new client's silo is provisioned/designated BEFORE the first client data write — never "temporarily" in a shared project. Residency exceptions require a joint operator+cai grant and must expire. Verify the write-target silo before ANY client data path goes live (standing pre-live residency gate).
- **LAYER-VOCAB-001** — bare product names ("ihsanos", "cosem") are INVALID as data references. Always name the layer — **frontend** (shared app) vs **data** — and for data name the exact store + `project ref` per `docs/data-store-registry.md` (e.g. "ihsanos multi-tenant DB `ceayjeamtmcyzzvqflus`" vs "irsyad silo (goumlyne) `goumlynecruxrlmzlntp`"). Data-writes/gate-requests carry the ref; layer-ambiguity in a data-path spec is a review FINDING, not style (same rejection class as an unpinned migration).
- **ORCH-TOPOLOGY-001** (2026-07-04, cai #6521 + orch #6523) — multiple orch bodies exist; the five singleton pens belong ONLY to the holder of the substrate `orch_lease` row (`orch-hub`; holder today = Studio hub): (i) draining/marking `to_agent='cc-orchestrator'` bus rows, (ii) lane prompt submission, (iii) watchdog, (iv) `tg_send`/tg-out + operator declarations, (v) fleet-status assertions. **Determine YOUR body before touching any pen** — `ORCH_BODY_ROLE` in `.env`: `console` = **Nazim**, the operator's CTO (Mac Mini, tmux session `nazim`; moved MacBook→Mini in the 2026-07-08 Nazim→Mini cutover) — replies to the operator IN-CONSOLE ONLY (his phone hears one voice: the hub's), never raw send-keys into lanes, directs the fleet via attributable bus rows, stamps only `tmux-console` operator_log rows; `hub`/unset = lease-holder doctrine (bridge section below). Enforced in code, not by promise: `scripts/lib/orch_lease.py` gates `tg_send`/`tg_send_file` fail-closed; `nervous_system/operator_log.py` auto-scopes reconciliation by body. DR takeover = `orch_lease.py take --reason` (CAS, loud) only when the hub is genuinely dead — a failed-over hub inherits pens, NOT the dead machine's tmux lanes.

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

The operator's 2-way bridge to cc-orchestrator (pure hub — operator talks to you, you delegate to lanes). Since the 2026-07-03 cutover, ONE daemon `nervous_system/ingest.py` (launchd `dev.wingmen.ingest`) long-polls every enabled channel in the `bot_channels` registry (dedupe → durable log → deny-by-default gate → route), and ONE gateway `nervous_system/tg_out.py` (launchd `dev.wingmen.tg-out`) owns outbound. Legacy `tg_bridge.py`/`cai_bridge.py` are RETIRED.

- **Inbound is nudge-only:** you get a count line (`📥 N unread on 'operator-orch' — reconcile operator_log.unprocessed()`), NEVER the payload. Read `operator_log.unprocessed()`, answer via `scripts/tg_send.sh "<reply>" [tag]`, then `operator_log.mark_handled_through(<max_id>)`. Your terminal answer alone does NOT reach the operator's phone.
- **CRITICAL (failures 2026-06-19, 2026-07-03; body-scoped by ORCH-TOPOLOGY-001 on 07-04):** a nudge can arrive MID-TASK as a harness *"the user sent a new message while you were working"* injection and is easy to miss. **HUB body only:** treat EVERY operator message as Telegram-bound — ALWAYS `tg_send` your reply; on the hub, the operator is on his phone. **Console body (Nazim): NEVER `tg_send`** — answer in-console; `orch_lease.py check` refuses pen (iv) when `ORCH_BODY_ROLE=console` (the 07-04 21:20Z violation, operator-caught, is why this is a gate and not a norm).
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
- **Never run `supabase db push` against production** (project_ref `ceayjeamtmcyzzvqflus`). Per CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS (decision 962): the CLI's shadow-diff path re-applies historic `CREATE OR REPLACE VIEW` statements from migrations whose view bodies pre-date current arms, silently stripping later arms from `boot_briefing`. Use the orch's direct psycopg-apply pattern instead — see PR #41 / #42 / #44 migration apply scripts. The shadow path is for local dev only.
