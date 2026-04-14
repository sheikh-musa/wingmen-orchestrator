# Handoff to dangerous-mode CC instance

**From:** CC-here (the careful instance)
**To:** Whichever Claude Code session Musa spawns next with `--dangerously-skip-permissions`
**Date:** 2026-04-14 ~17:10 SGT

## Read MEMORY.md first
`/Users/sheikhmusa/.claude/projects/-Users-sheikhmusa/memory/MEMORY.md`

Specifically these standing orders apply:
- **Take over failed jobs** if decision passed mutual review
- **Monitor every autocc push** until ARCH-016 Phase 0 clears
- **Keep cai updated** with CC-UPDATE-NNN rows in `strategic_decisions` (Supabase project `tscuymavysscrvoberrr`)
- **Plain-English titles** (no jargon like "swallowed-except harness")
- **Ship and tell** — don't pause for approval on small fixes

## Live state

**Queue (Supabase `jobs` table):**
- TASK-035 (job 72) — paused 2h, fail_count=3 — orchestrator-internal, dirty-tree casualty. Safe to manually rescue or requeue.
- TASK-036 (job 73) — paused 2h, fail_count=3 — same as above. Trivially: paste the `bug_pipeline_readiness` DDL from migration `task_032_add_category_parent_ref` (already applied) into `schema.sql`.
- TASK-022 (job 35) — paused, ihsanos, blocked on spec-gen issue (see below).
- TASK-030 (job 67) — paused, ihsanos, same blocker.

**Phase 0 progress (`bug_pipeline_readiness` table):**
- 3 green (ARCH-014 tests-move, ARCH-015 schema_gate, TASK-028 paused-job escalation)
- TASK-033 (zombie cleanup) — done, in `wingmen_orch.cleanup_zombie_jobs`
- TASK-034 (silent stall detector) — done, rescue commit `3d564c5`
- TASK-035 — pending implementation (paused job)
- TASK-036 — pending implementation (paused job)
- TASK-037 — flagged off-spec (mocked tests, not real harness). Needs TASK-037b for real `scripts/fire_drills/`.

## ACTIVE INVESTIGATION (where I left off)

**ihsanos spec-gen times out at 300s.** Two attempts I tried:
1. Switched `spec_generator.generate_spec` from `claude -p <argv>` to `claude -p - <stdin>` (commit pending — uncommitted). No fix.
2. Capped CLAUDE.md to 8000 chars in meta_prompt (commit pending — uncommitted). No fix at 90s — STILL timed out.

Currently running tiny-context repro at `/tmp/spec_repro3.py`. If that completes fast, the issue is content-based not size-based.

Suspect: the meta_prompt's framing ("the agent will receive your spec...") triggers the model to obsess about file access whenever the project context mentions large file structures. With small context the model spec'd around it. With ihsanos-scale context it loops.

**Possible next moves if you take this over:**
- Drop the "agent will receive your spec" framing entirely. Just ask "Produce a build spec with sections X, Y, Z..."
- Or: skip CLAUDE.md from meta_prompt entirely; tell the executing CC session to read it during build.
- Or: break spec_generator into a two-step: first ask CLI to read CLAUDE.md, then ask for spec.

## Uncommitted state

`spec_generator.py` has my in-flight changes (stdin + cap). Tests pass but live behavior unverified. Either commit + ship and watch, or revert and try a different angle.

## Safe-to-take-over

- TASK-035 implementation (write `nervous_system/error_tracker.py` with `track_exception` wrapper + counter + escalation; refactor 5 poll modules to use it; tests).
- TASK-036 implementation (paste DDL into `schema.sql`, ship; trigger ARCH-015 schema_gate as a live test of the gate).
- ihsanos spec-gen root cause if you have ideas.

## Don't touch

- Decisions ARCH-016, CAI-RESP-008 — mutual review with cai already closed, leave alone.
- Anything in `~/wingmen/projects/ihsanos` until spec-gen is fixed (or you fix it).

## Coordination

If we both take over the same job, last-writer-wins. Suggest:
- I keep ihsanos spec-gen + sweeps + cai updates
- You take TASK-035 + TASK-036 implementations + anything new that paged Musa

Drop a CC-UPDATE-NNN row when you take/finish something so I can see it.
