# wingmen-orchestrator STATUS

This file is no longer a changelog. The full history through 2026-07-08 is
archived at `docs/status-history.md` (591 lines, moved verbatim — nothing
lost, just no longer the thing a fresh body is told to trust as current).

**Where truth actually lives, live:**

- **`SELECT * FROM boot_briefing`** — the boot index every agent reads first.
  Repo context, open decisions, QA failures, recent session snippets.
- **The per-body handoff** — whichever body you are, your boot hook injects
  the newest handoff (`reports/<body>-handoff-NOW.md` or equivalent). That is
  your immediate "what's in flight" — not this file.
- **`fleet_lanes`** (Postgres table) — the lane roster: who's supposed to be
  up, on which worktree/branch, with which launcher. `scripts/lanes.sh ls`
  reads it live.
- **`agent_status` / lease tables** (`orch_lease`, `fleet_health_lease`) —
  who's actually alive and who holds which pen, right now.
- **`get_decision('<ref>')`** — full reasoning behind a specific ratified
  decision, on demand.

If you're tempted to hand-edit this file to record a change: don't. Write to
the substrate instead (a bus row, a decision, a handoff) — that's what the
next body actually reads.
