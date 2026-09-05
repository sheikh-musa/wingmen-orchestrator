# docs/ index

`status:` on every doc below is `living` (actively true, actively consulted),
`historical` (accurate record of past/completed work, not current guidance —
safe to read, don't treat as instructions), or `dead` (superseded by a later
ratified doc; following it would give a wrong answer). Compiled 2026-09-05
(op#19103 item 5d) by reference-grep + content read, not by title alone —
see the method note at the bottom.

## docs/ (top level)

- `data-store-registry.md` — status: **living**. Cited twice in `CLAUDE.md` doctrine (LAYER-VOCAB-001, the never-db-push rule); the canonical store/project-ref registry.
- `ihsan-gate.md` — status: **living**. Referenced by `nervous_system/ihsan_gate.py`.
- `irsyad-ground-truth.md` — status: **living**. "Every irsyad agent reads on boot," Nazim-owned, actively kept current.
- `lock-namespace.md` — status: **living**. Referenced by `scripts/lib/auto_agent_id.py`, `scripts/ci/check_lock_keys.sh`, `tests/test_check_lock_keys.py`.
- `migration-file-triage-manifest.md` — status: **historical**. Point-in-time file classification for the orch→gzb migration (op17195); the classification is done, not an ongoing reference.
- `orch-move-runbook.md` — status: **living**. The active runbook for the in-progress Mac Mini → gzb/wingmen-core migration (Musa op17195/17198) — this is the move Nazim's PR gating has been "paced behind" all through this cleanup.
- `secrets-dr-registry.md` — status: **living**. Canonical "is secret X backed up" lookup; explicitly designed to be kept current, not a snapshot.
- `self-improvement-loop-spec.md` — status: **living**. "Proposal, first slice building" as of 2026-08-15 — in-progress, not yet historical.
- `send-keys-automation-census-2026-07-03.md` — status: **living**. Actively maintained (tombstoned rows 5-7 in PR #85, 2026-09-05) rather than a frozen snapshot.
- `session-checkpoint-2026-07-03.md` — status: **historical**. A resume-state snapshot from a specific incident window; superseded by whatever `boot_briefing`/handoffs say today.
- `status-history.md` — status: **living**. The archived STATUS.md changelog (moved here 2026-09-05, item 5b) — living as the historical pointer target, not as current state.
- `substrate-as-product-roadmap.md` — status: **dead**. A 2026-06-23 draft awaiting cai ruling; superseded by the later, ratified `strategy/2026-h2-comprehensive-strategy.md` (V2, ratified 2026-07-05, explicitly "the canonical source"). Reading the roadmap for current priorities gives a stale answer.

## docs/plans/, docs/specs/ (top-level, pre-superpowers-convention)

- `plans/2026-04-09-bug-pipeline.md`, `specs/2026-04-09-bug-pipeline-design.md` — status: **historical**. Describe `bug_pipeline.py`, moved to `legacy/` 2026-09-05 (op#19103 item 4).
- `plans/2026-04-09-white-label-bots.md`, `specs/2026-04-09-white-label-bots-design.md` — status: **historical**. Describe `bot_manager.py`/`message_dispatcher.py`/friends, all moved to `legacy/` 2026-09-05.
- `specs/2026-04-10-self-service-automation-design.md` — status: **historical**. "Approved for implementation" 2026-04-10; the described billing/WordPress/domains work is long past its build window.
- `specs/2026-07-05-adcda-course-admin-engine.md` — status: **living**. Design proposal for the still-active `cosem-adcda` lane (fleet_lanes desired_state='up').

## docs/substrate/, docs/runbooks/

- `substrate/migration-conventions.md` — status: **living**. Referenced by `scripts/check_additive_migration.py`.
- `runbooks/lane-wedge-watchdog.md` — status: **living**. Describes `nervous_system/lane_wedge_watchdog.py`, a loaded launchd job (`dev.wingmen.lane-wedge-watchdog`).
- `runbooks/restart.md` — status: **living**. Describes `scripts/restart_orch.sh`, the only sanctioned restart path per `CLAUDE.md` Rules.

## docs/governance/

- `governance/api-vs-cli-architecture.md` — status: **living**. Standing architectural doctrine citing ratified decisions (643, 619); no evidence it's been superseded.
- `governance/inbox-check-directive.md` — status: **living**. "Canonical text — consumed by reference," cross-CC-family doctrine.

## docs/meetings/, docs/strategy/, docs/shipforge/

- `meetings/2026-07-06-hariz-syed-adcda-questions.md` — status: **historical**. Meeting-prep question list for a specific 2026-07-06 meeting.
- `shipforge/strategy-v0.md` — status: **dead**. "v0, shaping, pre-build" (2026-06-19); shipforge is now built and deployed (the 2026-09-05 audit reviews its live paywall), and the pricing question this doc raised was explicitly closed by `strategy/2026-07-05-shipforge-pricing-pressure-test.md`. Reading v0 for shipforge's current model/pricing gives a stale answer.
- `strategy/2026-07-05-financial-architecture.md` — status: **living**. Describes the still-live `finance_subscriptions` table (migration 018) and the still-up `finance` lane.
- `strategy/2026-07-05-shipforge-pricing-pressure-test.md` — status: **living**. The pricing floor it locked has not been superseded by a later doc.
- `strategy/2026-h2-comprehensive-strategy.md` — status: **living**. Self-declared "Living doc — canonical source," V2 ratified 2026-07-05, covers 2026 H2 (current period).

## docs/superpowers/{plans,specs,rollbacks,notes,runbooks}/ (52 files)

Every file here is a dated (2026-03-30 through 2026-07-08) implementation
plan, design spec, rollback note, or research note for a feature that has
since shipped (or been superseded) elsewhere in the tree — that's the
convention of this directory, not an exception. **status: historical** for
all 52, individually:

`plans/`: 2026-03-30-agent-split.md, 2026-03-30-persona-aware-routing.md, 2026-04-01-nervous-system-merge.md, 2026-04-19-arch-035-three-channel-taxonomy.md, 2026-04-19-bug-025-acceptance-path-trigger.md, 2026-04-19-governance-comms-pipeline-hardening.md, 2026-04-20-arch-036-priority-column.md, 2026-04-20-governance-hygiene-batch.md, 2026-04-20-step-3-launcher-multi-repo-identity.md, 2026-04-21-task-045-deploy-cc-family.md, 2026-04-24-batch-2-bug030-bridge-trigger-fix.md, 2026-04-24-orchestrator-status-001-agent-push-contract.md, 2026-04-28-orchestrator-status-001-option-b-implementation.md, 2026-05-08-synthetic-filter.md, 2026-05-17-cc-long-caller-registry-phase-a.md, 2026-05-21-long-caller-watchdog-phase-b.md, 2026-05-23-watchdog-phase-b-content-shape.md, 2026-05-27-cc-session-costs-auto-writer.md, 2026-06-05-cc-cai-daemon-phase-1.md (describes `cc_cai_daemon/`, deleted 2026-09-05 PR #85), 2026-06-10-tg-miniapp-e2e-orchestrator-half.md, 2026-06-11-bug024-phase2-identity-enforcement.md, 2026-06-11-bug035-reconciliation-primitive.md, 2026-06-11-cadence-008a-ihsanos-drain-worker.md (describes `ihsanos_drain/`, moved to `legacy/` 2026-09-05), 2026-06-13-reel-triage-v1.md (describes `reel_triage/`, moved to `legacy/` 2026-09-05), 2026-06-17-fleet-console-v1.md, 2026-06-27-gazzabyte-qa-slice-and-channels-crud.md, 2026-06-27-life-context-layer.md, 2026-06-28-zahidah-study-bot.md, 2026-07-01-db-decomposition.md, 2026-07-01-mac-mini-to-linux-cloud-migration.md, 2026-07-02-model-policy.md, 2026-07-02-unified-bot-ingest.md, 2026-07-02-window-blast-radius.md, 2026-07-08-cosem-platform-foundation.md.

`specs/`: 2026-03-30-agent-split-design.md, 2026-03-30-persona-aware-routing-design.md, 2026-04-01-nervous-system-merge-design.md, 2026-04-18-governance-comms-pipeline-hardening-design.md, 2026-04-19-arch-035-three-channel-governance-taxonomy-design.md, 2026-04-29-inbox-cadence-section-e-phase-3-design.md, 2026-05-08-synthetic-filter-design.md, 2026-06-11-bug024-phase2-identity-enforcement-design.md, 2026-06-11-bug035-reconciliation-primitive-design.md, 2026-06-17-fleet-console-v1-design.md, 2026-06-17-realtime-wake-111.md, 2026-07-08-cosem-modular-training-platform-vision.md.

`rollbacks/`: CC-LONG-CALLER-REGISTRY-001-phase-a.md, long-caller-watchdog-phase-b.md, watchdog-phase-b-content-shape.md.

`notes/`: 2026-07-08-cosem-exam-port-research.md, 2026-07-08-cosem-onboarding-port-research.md.

`runbooks/`: ci-live-db-setup.md.

## Totals

18 living, 2 dead, 60 historical, of 80 — close to the audit's "73/80
unreferenced, 6 dead" (this pass counts a `CLAUDE.md`-doctrine citation or a
still-live code reference as `living` even without a *code import*, which is
why the living count here is higher than the audit's "unreferenced" count;
the 2 confirmed-dead here are a subset of the audit's 6 — the other 4 need
the deeper per-claim read this mechanical pass didn't do).

## Method

For each doc: grep the whole repo (excl. `.venv/reports/logs/.claude/worktrees`)
for the filename in `.py`/`.sh` files and in `CLAUDE.md`; read the doc's own
first 5-10 lines for a self-declared status/date; cross-check against
`fleet_lanes`/`legacy/README.md` for whether the feature it describes is
still running or was just moved/deleted. Not a substitute for a full read of
every doc — flag any of the `historical` calls above that turn out to still
be load-bearing.
