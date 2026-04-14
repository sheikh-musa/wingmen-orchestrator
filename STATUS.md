# wingmen-orchestrator STATUS

Last Updated: 2026-04-14 15:30 SGT
Build Status: green
Deploy: N/A

## Last Completed Job
- Job #68: Add `category` and `parent_ref` columns to `strategic_decisions` — updated schema.sql with ALTER TABLE + indexes, added columns to select queries in strategic_decisions_poll.py and cai_review_request.py. DB migration blocked by Supabase MCP permissions — must be run manually.

## Result Summary
Added `category` (text) and `parent_ref` (text) columns to `strategic_decisions` table in schema.sql with partial indexes. Updated select strings in `strategic_decisions_poll.py` and `cai_review_request.py` to include new columns. 231 tests pass, 0 failures. Supabase MCP permission-blocked — migration SQL must be run manually against the live DB.

## Completed (Last 5)
- [green] Job #68: wingmen-orchestrator — Add category + parent_ref to strategic_decisions (2026-04-14, migration pending manual run)
- [green] Job #65: wingmen-orchestrator — [TASK-028] Paused job Telegram escalation — no more silent deaths (5m 18s, deploy: N/A)
- [green] Job #65: wingmen-orchestrator — [TASK-028] Paused job Telegram escalation with tiered alerts and dedup (2026-04-14)
- [green] Job #64: wingmen-orchestrator — [TASK-027] Pre-flight dirty-tree check before Claude Code runs (2026-04-14)
- [green] Job #63: wingmen-orchestrator — [TASK-026] Auto-flip strategic_decisions execution_status on job completion (2026-04-14)
- [green] Job #23: wingmen-orchestrator — repo_context_dump.py: cosem-tdu + cosem-adcda repo_memory populated (11 entries each, 2026-04-14)

##  Recent Jobs (auto-tracked)

Last Updated: 2026-04-14 15:05 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #65 | [TASK-028] Paused job Telegram escalation — no more silent deaths | green | N/A |
| #65 | [TASK-028] Paused job Telegram escalation — tiered alerts with dedup | green | N/A |
| #64 | [TASK-027] Pre-flight dirty-tree check before Claude Code runs | green | N/A |
| #63 | [TASK-026] Decision auto-flip on job completion — close the state-tracking gap | green | N/A |

---

## Recent Jobs (auto-tracked)

Last Updated: 2026-04-14 15:09 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #68 | [TASK-032] Add category + parent_ref columns to strategic_decisions — human-read | green | N/A |
