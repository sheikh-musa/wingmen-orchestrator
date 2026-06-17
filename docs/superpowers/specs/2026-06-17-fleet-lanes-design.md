# fleet_lanes — INTENT/CONFIG registry + `.lane.lock` collision guard

**Date:** 2026-06-17
**Ref:** CAI-RESP-255 #4 (amended after my challenge to CAI-RESP-253)
**Status:** schema drafted + dry-run validated; **awaiting operator sign-off before `--apply`**
**Target substrate:** `tscuymavysscrvoberrr` (orch), NOT prod `ceayjeamtmcyzzvqflus`

---

## The problem this solves

Lanes are currently declared in a hardcoded heredoc inside `lanes.sh`. That's fine
for one lane but doesn't scale, isn't queryable, and couples "what lanes exist" to a
shell script. We want a durable registry — but the naive version (a row per lane with
`pid` / `last_seen` / `status`) is exactly the drift trap I challenged in CAI-RESP-253.
A process that dies without writing back leaves a stale "running" row forever.

## Core invariant (non-negotiable)

> **The registry stores INTENT, never ACTUAL.** Liveness is DERIVED ON READ from the OS.

- `fleet_lanes` = declarative config: what lanes *should* exist, where, on what branch,
  with which launcher, and the desired run-state (`up`/`down`/`paused`).
- It holds **no** `pid`, `last_seen`, `status`, or `heartbeat`. Nothing self-reported.
- Actual liveness is computed at read time by the reconciler (below) from tmux + a
  `.lane.lock(pid)` file. Stale state is impossible because nothing persists actual.

## Part 1 — Schema (`migrations/002_fleet_lanes.sql`)

| column | type | notes |
|---|---|---|
| `lane` | `text` PK | stable name, e.g. `mirror`; `lanes.sh` keys off this |
| `worktree_path` | `text not null` | absolute dir the lane runs in |
| `branch` | `text` (nullable) | git branch; NULL for non-code nodes (e.g. cai) |
| `model` | `text not null` default `claude-opus-4-8` | |
| `launcher` | `text not null` default `launch_dangerous_cc.sh` | CHECK ∈ {`launch_dangerous_cc.sh`, `boot_cai.sh`} — captures the #2347 fork |
| `base_agent_id` | `text → agents(id)` (nullable) | family (engineer lanes) or exact id (strategic); informational |
| `desired_state` | `text not null` default `up` | CHECK ∈ {`up`,`down`,`paused`} — the reconcile target |
| `notes` | `text` | |
| `created_at` / `updated_at` | `timestamptz not null` default `now()` | |

Standard house style: RLS on + `service role full access` policy, `idx_fleet_lanes_desired`.

**Seed:** `mirror` (mirrors the current `lanes.sh` hardcoded block) + `cai`. The `cai`
launcher is now settled — `boot_cai.sh` ratified (CAI-RESP-256 Part B, RE #2347) — so its
row is included with `desired_state='down'`: it is operator-booted via `boot_cai.sh` after
the autonomy sign-off (CAI-RESP-255 #3), never reconciler-launched.

## Part 2 — `.lane.lock` collision guard + derived-liveness reconciler (code, TDD after schema)

This is the read path that makes the registry safe. No schema; it's launcher + `lanes.sh` code.

**`.lane.lock`** — a file written into `worktree_path` at boot holding the `claude` PID,
removed on clean exit (EXIT trap). It is the authoritative per-lane liveness signal.

- **Boot guard:** before booting lane X, if `worktree_path/.lane.lock` exists AND that PID
  is alive (`kill -0`), SKIP — a lane is already running there. This is the precise version
  of the "only failure mode that can actually lose work" guard `lanes.sh` already aims at
  (two dangerous auto-pushing CCs on one tree).
- **Stale lock:** lock present but PID dead → reclaimable; reconciler reports `down`,
  next `up` overwrites it.

**Reconciler (`lanes.sh ls`):** `SELECT … FROM fleet_lanes`, then for each lane derive
`actual` = (`.lane.lock` PID alive) — fallback to tmux/`pgrep`+`lsof` cwd-match for lanes
booted outside the lock protocol. Render `desired` vs `actual`. `lanes.sh up` boots every
`desired_state='up'` lane whose `actual` is down.

## What I am NOT doing in this slice

- Not auto-booting anything from the registry (operator/`lanes.sh up` stays the trigger).
- Not seeding or booting `cai` (gated on #2347).
- Not adding a write-back path for actual state (that's the anti-pattern this avoids).

## Apply procedure (after sign-off)

`python scripts/apply_fleet_lanes_migration.py` (dry-run, already validated) →
`--apply` to commit. Direct psycopg per decision-962; never `supabase db push`.
