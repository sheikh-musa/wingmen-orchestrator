# wingmen-orchestrator STATUS

Last Updated: 2026-04-14 15:30 SGT
Build Status: green
Deploy: N/A

## Last Completed Job
- Job #64: [TASK-027] Pre-flight dirty-tree check — added `_check_clean_tree()` helper and gate in `run_job()` between git tag and spec generation. Jobs now fail fast if repo has uncommitted/untracked files, preventing attribution theft in `git add -A`.

## Result Summary
New async helper `_check_clean_tree()` runs `git status --porcelain` with 30s timeout. Inserted check at step 2b in `run_job()` — after `_git_pull` + tag, before spec generation. Dirty tree raises RuntimeError with file list (truncated to 500 chars), matching the existing spec-validation failure pattern. Existing retry/fail_count logic handles the error automatically.

## Completed (Last 5)
- [green] Job #64: wingmen-orchestrator — [TASK-027] Pre-flight dirty-tree check before Claude Code runs (2026-04-14)
- [green] Job #63: wingmen-orchestrator — [TASK-026] Auto-flip strategic_decisions execution_status on job completion (2026-04-14)
- [green] Job #23: wingmen-orchestrator — repo_context_dump.py: cosem-tdu + cosem-adcda repo_memory populated (11 entries each, 2026-04-14)
- [green] Job #44: wingmen-orchestrator — [ARCH-004] Clean up dead code from ARCH-013 mutual-review upgrade (deploy: N/A)
- [green] Job #34: wingmen-orchestrator — [TASK-024] Semantic drift audit — LLM review on N-in-M sampled jobs (6m 16s, deploy: N/A)

##  Recent Jobs (auto-tracked)

Last Updated: 2026-04-14 15:30 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #64 | [TASK-027] Pre-flight dirty-tree check before Claude Code runs | green | N/A |
| #63 | [TASK-026] Decision auto-flip on job completion — close the state-tracking gap | red | N/A |

---

## Recent Jobs (auto-tracked)

Last Updated: 2026-04-14 14:54 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #64 | [TASK-027] Pre-flight working-tree clean check — prevent attribution theft | red | N/A |
