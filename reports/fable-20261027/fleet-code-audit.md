# Fleet code audit — orchestrator repo + Mac Mini launchd (+ gzb where visible)

- **Requested by:** Musa op#34969 (relayed by orch-console, bus #78605)
- **Auditor:** cc-substrate-3 (temporary Fable 5.1 lane, irsyad pool), 2026-10-27 19:11Z–20:50Z
- **Mode:** report only. Read-only on every store (substrate SELECT-only; no client DB touched). No PRs, no merges, no deploys. Seven parallel read-only subagents, each on one dimension; findings de-duplicated and re-ranked by the lane.
- **Base commit audited:** 7f10ed1 (merge of PR #545), worktree `orchestrator.wt-fable-3`.
- **Scope asked for:** PreToolUse hook/guard gaps · prose-only rules (enforce-in-code holes) · dead/duplicate scripts and watchdogs · launchd jobs whose script no longer exists · sleeping failure modes (except-pass, silent fallbacks) · bare `git pull` on the live checkout · worktrees without `.venv` · top-10 simplifications. Ranked by impact over effort, each with `file:line` evidence.

> Ranking key — **Impact** high/med/low = what breaks or how much pages/money/safety it touches. **Effort** S (<1h, one PR), M (half-day), L (multi-day/needs design). "Quick win" = high/med impact at S effort.

## 0. Executive summary (ranked quick wins)

_(filled after subagent returns)_

