# legacy/

Retired code. Nothing here runs — kept for history and for `git blame`, not
for import by anything live. Moved wholesale (`git mv`, history preserved)
under op#19103 item 4, 2026-09-05.

**Why it's safe to leave dead:** the entire cluster is reachable only through
`wingmen_orch.py`, and `wingmen_orch.py` itself has no launchd job on either
the Mac Mini or the VPS hub (verified by orch-console on both hosts,
2026-09-05) and STATUS.md:7 has said "legacy wingmen_orch.py disabled" since
before this move. If you're reading this because a fresh body went looking
for one of these files: it isn't coming back without a deliberate decision
to revive it, and reviving it means re-pointing its imports back out of
`legacy.*` first.

**Import path:** everything in here that used to import a sibling by a bare
top-level name (`from bot_manager import ...`) now imports it as
`legacy.<name>` (`from legacy.bot_manager import ...`), since moving into a
subdirectory changed the module path. Imports of modules that stayed at
root (`ai_provider`, `context_loader`, `notification_router`, `test_gate` —
see below) are untouched; those still resolve normally.

## What's here, what retired it, when

- **The Telegram-bot-per-client orchestrator** (`wingmen_orch.py`,
  `cto_bot.py`, `message_dispatcher.py`, `personality.py`, `group_setup.py`,
  `bot_manager.py`, `bot_onboarding.py`, `bot_user_resolver.py`,
  `permissions.py`, `agents/`, `handlers/`) — the original architecture
  where the orchestrator itself ran each client's Telegram bot via
  `wingmen_orch.py`'s main loop. Disabled per STATUS.md:7; no launchd job
  (`dev.wingmen.orchestrator` not loaded on either host). Superseded by the
  current bus/lane/ingest architecture documented in the top-level
  `CLAUDE.md`.
- **Its supporting one-off modules** (`bug_notifier.py`, `bug_pipeline.py`,
  `deploy_manager.py`, `ralph_runner.py`, `webhook_server.py`,
  `heartbeat.py`, `watchdog.py`, `uptime_monitor.py`, `approval_handler.py`,
  `build_audit.py`, `conversation.py`, `council_commands.py`,
  `invite_manager.py`, `provisioner.py`, `semantic_drift.py`,
  `spec_generator.py`, `status_reporter.py`, `tools_command.py`) —
  reachable only via `wingmen_orch.py`/`cto_bot.py`; no other importer
  anywhere in the repo, verified by full-repo grep before this move.
- **`storefront/`** (`platform_bot.py`, `miniapp.py`, `onboarding.py`,
  `slug.py`) — orchestrator-side half of the shared-bot storefront + Mini
  App (build #60, 2026-06-10). Superseded by the ihsanos repo per
  IHSANOS-STOREFRONT-TG-001 (CLAUDE.md: "ihsanos ... owns storefront
  backend + Telegram surface"); the orchestrator-side half was never the
  surviving one. Reachable only via `wingmen_orch.py:1615`.
- **`ihsanos_drain/`** — CADENCE-008A drain worker for the ihsanos repo;
  superseded by the current per-repo lane architecture.
- **`reel_triage/`** — the reel-inbox triage pipeline; retired with its
  Telegram surface. `run_reel_digest.py` (a `reel_triage` consumer, missed
  in the first pass, added on review) lives at `legacy/reel_triage/
  run_reel_digest.py` rather than a new `legacy/scripts/` — it's entirely
  built on this package, not a general-purpose script. Its plist,
  `dev.wingmen.reel-worker.plist` (`reel_triage.worker`), moved to
  `legacy/ops/launchd/` with the other two relics.

## Explicitly NOT moved (still live at root)

`ai_provider.py`, `context_loader.py`, `notification_router.py` have real
importers under `nervous_system/` (council_summary, ecosystem_auditor,
morning_brief, council_relay, wingmen_dream, ux_analysis, weekly_digest,
schema_gate, council_agent, swallowed_except_harness, bug_escalation,
paused_job_escalation, queue_stall_detector) — whether those `nervous_system/`
callers are themselves live is a separate, ongoing question orch-console owns
directly; this move stops at the root boundary. `test_gate.py` stays too
(consumed by `scripts/fire_drills/drill_missing_tool.py` and referenced in
the substrate ship-gate discussion).

## Tests

Moved alongside into `tests/legacy/` (mirroring this layout) with a
module-level `pytestmark = pytest.mark.skip(...)` on every test file, so the
suite still collects (`pytest --collect-only` is clean) without ever
executing retired-code tests. `tests/legacy/reel_triage/conftest.py` and
`test_no_ig_creds.py` had a `parents[N]` path depth hardcoded to the old
2-levels-deep location under `tests/`; bumped to 3 for the new
`tests/legacy/reel_triage/` nesting.
