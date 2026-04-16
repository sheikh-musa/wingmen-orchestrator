# wingmen-orchestrator STATUS

Last Updated: 2026-04-16 19:24 SGT
Build Status: green
Deploy: N/A

## Last Completed Job
- Job #85: [BUG-018] strategic_decisions_poll re-queueing shipped decisions — added `evidence_commit_sha IS NULL` + `challenge_status <> 'implemented'` guard to candidate query.

## Result Summary
Appended `.is_("evidence_commit_sha", "null")` and `.neq("challenge_status", "implemented")` to the single candidate-decision query in `nervous_system/strategic_decisions_poll.py:47` — the only place the poller scans `strategic_decisions`. No other poll-path changes. The filter is defense-in-depth for the TASK-026 auto-flip (which writes `challenge_status='implemented'`) and the ARCH-024 evidence-commit backfill (which writes `evidence_commit_sha`): once either signal lands on a TASK-*/BUG-* row, the poll will not re-enqueue it even if `notified_at` later gets cleared. Supabase MCP candidate-set delta (simulating the same WHERE clauses with and without the new pair, ignoring `notified_at` to isolate the guard): 41 → 41 today (0 TASK-*/BUG-* rows currently have either shipped signal set), but `COUNT(*) WHERE evidence_commit_sha IS NOT NULL OR challenge_status='implemented' = 10` system-wide (all ARCH-* today) — as TASK-026 starts flipping TASK/BUG refs, this guard engages. Added `tests/test_strategic_decisions_poll.py` with three tests: asserts `.is_("evidence_commit_sha","null")` + `.neq("challenge_status","implemented")` appear in the query chain, asserts no `jobs.insert` when the filter returns zero rows, asserts an open decision still enqueues. Full suite: 361 pass, 1 pre-existing fail (`test_drill_dirty_tree_rejection`, unrelated) — delta +3 tests. Conftest mock extended with `.or_` / `.neq` returns. Restart deferred: this session runs as a child of the orchestrator launchd service (`ps -o ppid` → orchestrator PID 1087), so calling `launchctl kickstart -k` from the worktree would SIGTERM the parent before ralph_runner's `_merge_and_remove_worktree` lands `ralph-job-85` on `main` — which would both abort this job AND reload stale code. Ralph_runner merges on session completion; the restart (`scripts/restart_orch.sh`) must fire from the orchestrator's post-job hook or be run by Musa after merge.

## Completed (Last 5)
- [green] Job #85: wingmen-orchestrator — [BUG-018] strategic_decisions_poll shipped-decision filter — evidence_commit_sha IS NULL + challenge_status != 'implemented' (deploy: N/A)
- [red] Job #90: wingmen-orchestrator — [SMOKE-001] BUG-019 worktree isolation smoke test — append a comment to STATUS.md (3m 0s, deploy: N/A)
- [green] Job #83: wingmen-orchestrator — [BUG-016] Safe-restart procedure — launchctl kickstart helper + runbook; nohup forbidden (deploy: N/A)
- [green] Job #82: wingmen-orchestrator — [BUG-015] Graceful shutdown asyncio cleanup — cancel pending tasks before loop close (deploy: N/A)
- [green] Job #79: wingmen-orchestrator — [BUG-012] Gate 6 Haiku empty-JSON fix — ANTHROPIC_API_KEY guard + fail-loud (deploy: N/A)

##         Recent Jobs (auto-tracked)

Last Updated: 2026-04-16 18:48 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #65 | [TASK-028] Paused job Telegram escalation — no more silent deaths | green | N/A |
| #65 | [TASK-028] Paused job Telegram escalation — tiered alerts with dedup | green | N/A |
| #64 | [TASK-027] Pre-flight dirty-tree check before Claude Code runs | green | N/A |
| #63 | [TASK-026] Decision auto-flip on job completion — close the state-tracking gap | green | N/A |

---

## Recent Jobs (auto-tracked)

Last Updated: 2026-04-16 19:05 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #85 | [BUG-018] strategic_decisions_poll queues jobs for already-shipped decisions — a | red | N/A |
| #85 | [BUG-018] strategic_decisions_poll shipped-decision filter — evidence_commit_sha IS NULL + challenge_status != 'implemented' | green | N/A |
| #90 | [SMOKE-001] BUG-019 worktree isolation smoke test — append a comment to STATUS.m | red | N/A |
| #84 | [BUG-013] qa_findings.created_at migration — column + index added, bridge unblocked | green | N/A |
| #83 | [BUG-016] Safe-restart procedure — launchctl kickstart helper + runbook; nohup forbidden | green | N/A |
| #82 | [BUG-015] Graceful shutdown asyncio cleanup — cancel pending tasks before loop close | green | N/A |
| #79 | [BUG-012] Gate 6 Haiku empty-JSON fix — ANTHROPIC_API_KEY guard + fail-loud | green | N/A |
| #71 | Queue stall detector — alert CTO on 30min+ queued jobs with dedup | green | N/A |
| #70 | [TASK-033] Zombie running-row cleanup on orchestrator startup | green | N/A |
| #69 | [TASK-037] Fire drill harness — 5 scenarios exercised and documented | green | N/A |
