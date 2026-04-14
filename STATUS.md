# wingmen-orchestrator STATUS

Last Updated: 2026-04-14 17:00 SGT
Build Status: green
Deploy: N/A

## Last Completed Job
- Job #70: Startup zombie job cleanup — mark all `status='running'` jobs as `failed` on orchestrator startup so zombies from prior crashes don't block the queue.

## Result Summary
Added `cleanup_zombie_jobs()` function (no time cutoff, marks as `failed` with "Zombie:" prefix). Called once in `main_loop()` startup before the poll loop. 3 new tests added (TestCleanupZombieJobs), 253 total tests pass, 0 failures.

## Completed (Last 5)
- [green] Job #70: wingmen-orchestrator — Startup zombie job cleanup — mark running jobs as failed on restart (deploy: N/A)
- [green] Job #69: wingmen-orchestrator — [TASK-037] Fire drill harness — 5 scenarios exercised and documented (7m 55s, deploy: N/A)
- [green] Job #61: wingmen-orchestrator — [BUG-006] cc_work_sessions not being written — 1 row from 20+ jobs. Narrative layer is dead. (3m 36s, deploy: N/A)
- [green] Job #61: wingmen-orchestrator — [BUG-006] Wire _write_work_session into success/failure/crash paths (2026-04-14)
- [green] Job #60: wingmen-orchestrator — [TASK-025] Add uptime monitoring: ping ihsanOS and cosem-tdu every 5 min, Telegram alert on failure (7m 44s, deploy: N/A)

##      Recent Jobs (auto-tracked)

Last Updated: 2026-04-14 16:12 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #65 | [TASK-028] Paused job Telegram escalation — no more silent deaths | green | N/A |
| #65 | [TASK-028] Paused job Telegram escalation — tiered alerts with dedup | green | N/A |
| #64 | [TASK-027] Pre-flight dirty-tree check before Claude Code runs | green | N/A |
| #63 | [TASK-026] Decision auto-flip on job completion — close the state-tracking gap | green | N/A |

---

## Recent Jobs (auto-tracked)

Last Updated: 2026-04-14 16:17 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #70 | [TASK-033] Zombie running-row cleanup on orchestrator startup | green | N/A |
| #69 | [TASK-037] Fire drill harness — 5 scenarios exercised and documented | green | N/A |
| #61 | [BUG-006] cc_work_sessions not being written — 1 row from 20+ jobs. Narrative la | green | N/A |
| #60 | [TASK-025] Add uptime monitoring: ping ihsanOS and cosem-tdu every 5 min, Telegr | green | N/A |
| #68 | [TASK-032] Add category + parent_ref columns to strategic_decisions — human-read | green | N/A |
