# wingmen-orchestrator STATUS

Last Updated: 2026-04-14 SGT
Build Status: green
Deploy: N/A

## Last Completed Job
- Job #71: Queue stall detector — alert CTO when jobs sit in `status='queued'` for 30min+ with dedup via `notification_log` and recovery sweep.

## Result Summary
Created `nervous_system/queue_stall_detector.py` with `check_queue_stalls()` — queries stalled queued jobs, dedup via notification_log, sends Telegram alert to CTO, recovery sweep clears dedup when jobs leave queued status. Wired into `wingmen_orch.py` main loop (60-poll interval). 5 new tests added, 258 total tests pass, 0 failures.

## Completed (Last 5)
- [green] Job #71: wingmen-orchestrator — Queue stall detector with dedup and recovery sweep (deploy: N/A)
- [green] Job #70: wingmen-orchestrator — [TASK-033] Zombie running-row cleanup on orchestrator startup (4m 8s, deploy: N/A)
- [green] Job #69: wingmen-orchestrator — [TASK-037] Fire drill harness — 5 scenarios exercised and documented (7m 55s, deploy: N/A)
- [green] Job #61: wingmen-orchestrator — [BUG-006] cc_work_sessions not being written — 1 row from 20+ jobs. Narrative layer is dead. (3m 36s, deploy: N/A)
- [green] Job #61: wingmen-orchestrator — [BUG-006] Wire _write_work_session into success/failure/crash paths (2026-04-14)

##       Recent Jobs (auto-tracked)

Last Updated: 2026-04-14 16:17 SGT

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
| #71 | Queue stall detector — alert CTO on 30min+ queued jobs with dedup | green | N/A |
| #70 | [TASK-033] Zombie running-row cleanup on orchestrator startup | green | N/A |
| #69 | [TASK-037] Fire drill harness — 5 scenarios exercised and documented | green | N/A |
| #61 | [BUG-006] cc_work_sessions not being written — 1 row from 20+ jobs. Narrative la | green | N/A |
| #60 | [TASK-025] Add uptime monitoring: ping ihsanOS and cosem-tdu every 5 min, Telegr | green | N/A |