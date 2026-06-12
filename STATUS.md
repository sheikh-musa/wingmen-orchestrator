# wingmen-orchestrator STATUS

Last Updated: 2026-06-13 SGT (Reel Triage v1 BUILD COMPLETE — 12/12 TDD tasks, 45/45 green, branch feat/reel-triage-v1; reversible-only, go-live operator-gated)
Build Status: green
Deploy: n/a (feature-flagged module, default OFF; no deploy — Mac Mini bot + Mac Studio worker host)

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