# wingmen-orchestrator STATUS

Last Updated: 2026-04-16 SGT
Build Status: green
Deploy: N/A

## Last Completed Job
- Job #84: [BUG-013] qa_findings.created_at missing — migration adds column (NOT NULL DEFAULT now()) + index; QA bridge poll no longer errors.

## Result Summary
Applied migration `bug013_qa_findings_created_at` via Supabase MCP: `ALTER TABLE qa_findings ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now()` plus `CREATE INDEX IF NOT EXISTS qa_findings_created_at_idx ON qa_findings (created_at DESC)`. The `DEFAULT now()` backfilled all 500 pre-existing rows in a single statement (verified: 0 null rows). Updated `schema.sql` to rename the existing index declaration to `qa_findings_created_at_idx` so it mirrors the live DB. QA bridge (`nervous_system/qa_bridge.py`) required no code change — its `.order("created_at", desc=False)` FIFO SELECT now succeeds; the bridge doesn't INSERT into `qa_findings` (external QA producers do). Added regression test `test_select_orders_by_created_at` in `tests/test_qa_bridge.py` asserting `.order("created_at", ...)` is called. Full suite: 355 pass (350 prior + 5 new). Smoke test via MCP: insert legacy-schema row → `SELECT ... ORDER BY created_at DESC LIMIT 1` returns the inserted row with populated `created_at` — no error. Note: broader qa_findings schema drift (legacy `repo`/`role`/`flow` columns vs schema.sql's `repo_name`/`source`/`title`) is pre-existing and out of BUG-013 scope — tracked in build_log id=66.

## Completed (Last 5)
- [green] Job #83: wingmen-orchestrator — [BUG-016] Safe-restart procedure — launchctl kickstart helper + runbook; nohup forbidden (deploy: N/A)
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
| #84 | [BUG-013] qa_findings.created_at migration — column + index added, bridge unblocked | green | N/A |
| #83 | [BUG-016] Safe-restart procedure — launchctl kickstart helper + runbook; nohup forbidden | green | N/A |
| #82 | [BUG-015] Graceful shutdown asyncio cleanup — cancel pending tasks before loop close | green | N/A |
| #79 | [BUG-012] Gate 6 Haiku empty-JSON fix — ANTHROPIC_API_KEY guard + fail-loud | green | N/A |
| #71 | Queue stall detector — alert CTO on 30min+ queued jobs with dedup | green | N/A |
| #70 | [TASK-033] Zombie running-row cleanup on orchestrator startup | green | N/A |
| #69 | [TASK-037] Fire drill harness — 5 scenarios exercised and documented | green | N/A |
| #61 | [BUG-006] cc_work_sessions not being written — 1 row from 20+ jobs. Narrative la | green | N/A |