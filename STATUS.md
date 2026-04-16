# wingmen-orchestrator STATUS

Last Updated: 2026-04-16 SGT
Build Status: green
Deploy: N/A

## Last Completed Job
- Job #83: [BUG-016] Safe-restart procedure — `scripts/restart_orch.sh` via `launchctl kickstart -k`, runbook, `nohup` forbidden.

## Result Summary
Added module-level `_cancel_pending_tasks(loop)` helper in `wingmen_orch.py` and wired it into the existing `finally:` block before `loop.close()` so any tasks still pending after `run_until_complete` returns (e.g. the `_shutdown` task itself, or tasks spawned inside cancellation cleanup) are cancelled and awaited via `asyncio.gather(..., return_exceptions=True)`. Eliminates the "Task was destroyed but it is pending" warnings previously emitted at SIGINT/SIGTERM. Added `tests/test_graceful_shutdown.py` with 3 tests (cancel background task, swallow exceptions, no-op when empty). Full suite: 350 pass (347 prior + 3 new). Synthetic SIGINT smoke exercised a late-spawned cleanup task and reported 0 leak warnings.

## Completed (Last 5)
- [green] Job #82: wingmen-orchestrator — [BUG-015] Graceful shutdown asyncio cleanup — cancel pending tasks before loop close (deploy: N/A)
- [green] Job #79: wingmen-orchestrator — [BUG-012] Gate 6 Haiku empty-JSON fix — ANTHROPIC_API_KEY guard + fail-loud (deploy: N/A)
- [green] Job #35: ihsanos — [TASK-022] Re-measure BUG-005 hydration with per-stage instrumentation + production build (9m 32s, deploy: https://ihsandms-qk0oxeq1y-musaaaaaaas-projects.vercel.app)
- [green] Job #71: wingmen-orchestrator — Queue stall detector with dedup and recovery sweep (deploy: N/A)
- [green] Job #70: wingmen-orchestrator — [TASK-033] Zombie running-row cleanup on orchestrator startup (4m 8s, deploy: N/A)
- [green] Job #69: wingmen-orchestrator — [TASK-037] Fire drill harness — 5 scenarios exercised and documented (7m 55s, deploy: N/A)

##        Recent Jobs (auto-tracked)

Last Updated: 2026-04-14 19:49 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #65 | [TASK-028] Paused job Telegram escalation — no more silent deaths | green | N/A |
| #65 | [TASK-028] Paused job Telegram escalation — tiered alerts with dedup | green | N/A |
| #64 | [TASK-027] Pre-flight dirty-tree check before Claude Code runs | green | N/A |
| #63 | [TASK-026] Decision auto-flip on job completion — close the state-tracking gap | green | N/A |

---

## Recent Jobs (auto-tracked)

Last Updated: 2026-04-16 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #83 | [BUG-016] Safe-restart procedure — launchctl kickstart helper + runbook; nohup forbidden | green | N/A |
| #82 | [BUG-015] Graceful shutdown asyncio cleanup — cancel pending tasks before loop close | green | N/A |
| #79 | [BUG-012] Gate 6 Haiku empty-JSON fix — ANTHROPIC_API_KEY guard + fail-loud | green | N/A |
| #71 | Queue stall detector — alert CTO on 30min+ queued jobs with dedup | green | N/A |
| #70 | [TASK-033] Zombie running-row cleanup on orchestrator startup | green | N/A |
| #69 | [TASK-037] Fire drill harness — 5 scenarios exercised and documented | green | N/A |
| #61 | [BUG-006] cc_work_sessions not being written — 1 row from 20+ jobs. Narrative la | green | N/A |