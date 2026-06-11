# BUG-035 Reconciliation Primitive — Design

**Date:** 2026-06-11
**Owner:** cc-orchestrator (substrate)
**Authority:** CAI-RESP-205 (ratifies BUG-035 diagnosis; scopes this build)

## Problem

A cai ruling can be delivered to an agent and marked `read_at`, yet the
dependent blocking task is never reconciled — so a chase re-fires for an
already-answered ask ("read != reconciled"). Root cause (ratified): there is
no schema association between a ruling and the work item it unblocks, and the
blocking task lives only in the blocked agent's local session (e.g. cc-ihsanos
task #55), so no view can check task-cleared. `read_at` is batch-drain-granular,
not per-ruling consumption.

## Scope (hard fence from CAI-RESP-205)

Build ONLY the minimal substrate primitive for **cross-agent BLOCKING
handoffs**: a ruling unblocks a task and reconciliation must be a *checked
state*, not a convention. Do NOT build a general task manager; local in-session
tasks stay local. Same SIMPLICITY discipline as the rest of the substrate.

`reconciled_at` must be set by an explicit owner close, NOT auto-stamped when a
ruling merely exists — auto-stamping on ruling-existence reproduces the exact
read≠reconciled bug.

## Design

### 1. Table `blocking_tasks`

A minimal substrate row giving each cross-agent blocker an id to key against.

| column | type | notes |
|---|---|---|
| `id` | bigserial PK | the substrate id rulings reference |
| `owner_agent` | text NOT NULL | the blocked agent who must act once unblocked |
| `created_by` | text NOT NULL | the agent who raised the blocker |
| `subject` | text NOT NULL | short blocker title |
| `detail` | text | optional longer context |
| `thread_id` | uuid | bus thread the blocker lives on (nullable) |
| `status` | text NOT NULL default `'open'` | CHECK in (`open`,`reconciled`,`cancelled`) |
| `created_at` | timestamptz NOT NULL default now() | |
| `reconciled_at` | timestamptz | the checked state — set on explicit close |
| `reconciled_by_decision_ref` | text | which ruling reconciled it |
| `is_test` | boolean NOT NULL default false | hygiene per SUBSTRATE-COHERENCE-001 B |

`owner_agent` / `created_by` reuse the canonical agent identity set
(SUBSTRATE-COHERENCE-001 E) but are NOT CHECK-constrained here — the
`from_agent` canon CHECK is still deferred pending the ralph_runner /
arch-030-escalation ruling, and we will not add a stricter constraint on a new
table than the bus itself carries.

### 2. Ruling → task link

Add `unblocks_task_id bigint` to `strategic_decisions` (nullable;
REFERENCES `blocking_tasks(id)`). A ruling that clears a specific blocker
carries that id (cai requirement #1).

### 3. Reconciliation = explicit owner close

`reconcile_blocking_task(task_id, decision_ref)`:
- sets `status='reconciled'`, `reconciled_at=now()`,
  `reconciled_by_decision_ref=decision_ref`
- idempotent: reconciling an already-reconciled task is a no-op success
- returns False for unknown / cancelled task ids

This is the checked state. The owner calls it when they have actually consumed
the ruling and cleared their blocker.

### 4. Blocker view `open_blocking_tasks`

```
SELECT bt.*, sd.decision_ref AS unblocking_ruling_ref,
       (sd.decision_ref IS NOT NULL) AS ruling_issued
FROM blocking_tasks bt
LEFT JOIN strategic_decisions sd ON sd.unblocks_task_id = bt.id
WHERE bt.status = 'open' AND bt.is_test IS NOT TRUE;
```

The BUG-035-killing signal: a row with `ruling_issued = true` but the task
still `open` (no `reconciled_at`) = "ruling delivered but not reconciled" — the
actionable nudge, distinct from `read_at`. A row with `ruling_issued = false`
= genuinely still waiting on cai.

### 5. boot_briefing arm

Add an `open_blocking_tasks` arm to `boot_briefing` so digests / chases key on
task-cleared, not delivery. Excludes `is_test` (consistent with B). Applied via
the arm-level psycopg-apply surgery pattern (decision-962 safe), NOT
`supabase db push`.

### 6. Helper module `nervous_system/blocking_tasks.py`

Thin CRUD over the table, matching house style (module docstring, psycopg,
explicit dsn param):
- `create_blocking_task(dsn, *, owner_agent, created_by, subject, detail=None, thread_id=None, is_test=False) -> int`
- `reconcile_blocking_task(dsn, task_id, decision_ref) -> bool`
- `list_open_blocking_tasks(dsn, *, include_test=False) -> list[dict]`

## Out of scope (deferred / other owners)

- **Adoption** — cc-ihsanos creating/closing their task #55-class blockers via
  this primitive is their integration half. Handoff note posted to the bus.
- **Auto-reconcile** — explicitly rejected (reproduces the bug).
- **Wiring into the orchestrator inbox-loop chase logic** — no orchestrator-side
  blocker exists to track yet; YAGNI until one does.
- **from_agent / owner_agent CHECK constraints** — deferred with the bus's own
  canon CHECK.

## Testing

DB-touching code uses the repo's real-DSN integration pattern
(`pytestmark = skipif(not DSN)`, psycopg autocommit, fixtures that clean up
their own rows by id/thread). TDD throughout. Schema + view changes verified
by a dry-run/apply script asserting the expected post-state before commit.

## Apply / migration

All schema and view changes ship as a psycopg-apply script under `scripts/`
(dry-run then `--apply`), matching `apply_sla_is_test.py` /
`apply_boot_briefing_diet.py`. Never `supabase db push` to prod (CLAUDE.md
decision-962).
