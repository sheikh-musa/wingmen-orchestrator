# Memory 2.0 item 2: shared fleet memory + per-repo code map

Owner: cc-substrate. op#35477. Due 2026-10-29 12:00Z.

Driven by Wan's op#35469 (irsyad coord re-asked 14 already-answered questions
in 7 days) and Musa's op#35474 (tracking growing codebases). Finding:
`repo_context.architecture_summary` is empty for every repo — only
`recent_changes` ever gets refreshed.

Coordination: item 1 (`cc-paperclip-spike-1`, governance floors, migrations
131/132, `decision_log` due Fri 2026-11-06) established the **audited-row
pattern** (migration 131: `op_ref text NOT NULL`; `retired_at`/`retired_op_ref`
with a CHECK pairing them; append-only audit table locked down by REVOKE-then-
GRANT, not convention; a `SECURITY DEFINER` trigger with pinned `search_path`
writes the audit row). Per the "one pattern, not two" directive, both tables
below reuse this pattern rather than inventing a second one.

## A. Shared fleet memory

A substrate table, not files: a fact told to one agent becomes readable by
every agent at boot and before acting. Store: the **orchestrator substrate,
project ref `tscuymavysscrvoberrr`** (per `docs/data-store-registry.md`,
LAYER-VOCAB-001 — this is the same database `boot_briefing` lives in, not a
client silo).

- `fleet_memory(id, scope, scope_key, fact text, op_ref text NOT NULL,
  created_by, created_at, supersedes_id, retired_at, retired_op_ref,
  CHECK (retired_at IS NULL OR retired_op_ref IS NOT NULL))`
  - `scope` ∈ `fleet | client | repo`; `scope_key` is null for fleet-wide,
    else a client slug or repo name. Scoping is columns, not separate tables,
    so one query covers "everything relevant to me."
  - `fleet_memory_audit`: append-only via REVOKE-ALL-then-GRANT-INSERT,
    written by the same `SECURITY DEFINER` trigger shape as migration 131.
  - Supersession is explicit, never inferred: `supersedes_id` is a nullable
    FK to `fleet_memory.id`, set BY THE WRITER at insert time (the writer
    names which row it replaces — there is no "overlapping subject"
    heuristic). A trigger sets the named row's `retired_at = now()` and
    `retired_op_ref = NEW.op_ref` when `supersedes_id` is set. Audit trail
    stays intact; readers only ever see non-retired rows by default.
  - Write path (who may INSERT/retire): INSERT via a `SECURITY DEFINER`
    function only (no direct table grants to agent roles), which stamps
    `created_by` from the caller's resolved agent identity — fail-closed,
    same shape as `bus_send.py`'s identity resolution; a call with no
    resolvable identity is refused, not defaulted. RLS: `fleet_memory_insert`
    policy restricted to that function's role; plain SELECT is open to every
    agent role (read-all is the point). No role gets table-level
    UPDATE/DELETE — retiring a row only happens through the supersede path
    above, never a bare UPDATE.
- Integrates with, does not replace, per-agent memory files: a lane's own
  `MEMORY.md`/memory files stay for personal working notes; `fleet_memory` is
  for facts that must survive a `/clear` and reach OTHER agents. Boot sequence
  gets one more step: `SELECT * FROM fleet_memory WHERE retired_at IS NULL
  AND (scope = 'fleet' OR scope_key IN (<my repos/clients>))` — note the
  parens: written as `retired_at IS NULL AND scope = 'fleet' OR scope_key IN
  (...)` it returns retired fleet-scope rows (AND binds tighter than OR, so
  the retired-filter only applies to the first disjunct). Add a regression
  test seeding one retired fleet-scope row and asserting it is excluded.
- CAI-1034: rows store rule TEXT + op# refs only. No client data (names, IDs,
  transactions) ever lands in `fact` or `scope_key` beyond a client *slug*.
- Migration: direct psycopg-apply only, from a worktree PR to
  `fable/substrate-safe-fixes`, console-gated before apply — never
  `supabase db push` against any production store.

## B. Per-repo code map

Generated from code (not hand-maintained prose), refreshed on every merge,
with a staleness alarm.

- Populates the existing but-empty `repo_context.architecture_summary`
  column — no new TABLE needed here, unlike (A). There IS still a small
  migration: `repo_context` needs one new column,
  `architecture_summary_updated_at timestamptz`, added in the same
  slice-1 console-gated, direct-psycopg migration as (A)'s `fleet_memory`
  tables (two changes, one migration file).
- Generator: a script run post-merge (git hook or CI step on the repo, not a
  human) that walks the repo's top-level structure + entry points and writes
  a short structured summary (modules, owned tables/services, key entry
  files) — scoped small on purpose, not a full AST dump.
- Staleness alarm: `cc-fleet-health`'s existing sweep pattern (it already
  runs a periodic self-heal sweep for sequence drift etc.) adds a check that
  pages if a repo's `architecture_summary_updated_at` is older than its
  latest merged commit.
- Starting repos (smallest blast radius first): `cosem-adcda`,
  `cosem-platform`, `ihsanos`.

## C. Slices, with honest dates

1. **2026-10-31** — one migration: `fleet_memory` + `fleet_memory_audit`
   (with the `SECURITY DEFINER` insert function, RLS policy, and the
   supersede trigger) plus `repo_context.architecture_summary_updated_at`,
   written, console-gated, applied direct-psycopg. First rows seeded: the
   irsyad standing client-rules seed list (#80901) as `scope=client,
   scope_key=irsyad`, rule text + op refs only (CAI-1034), inserted through
   `cc-irsyad-coord` per the console/irsyad execution-separation rule —
   strongest first-slice candidate since it's already costing coord re-asks
   today, and it's the smallest possible slice (seed a handful of existing
   rows, no new generator to build).
2. **2026-11-03** — boot-sequence read step wired in (one `SELECT` added to
   the existing boot_briefing-style flow); at least one lane (coord) reading
   `fleet_memory` instead of being re-asked.
3. **2026-11-07** — code-map generator for ONE repo (`cosem-adcda`, smallest)
   landed and `architecture_summary` populated; staleness check stubbed.
4. **2026-11-14** — generator extended to `cosem-platform` + `ihsanos`;
   staleness alarm live in cc-fleet-health's sweep.

No code or migrations are part of this deliverable — plan only, per handoff.
Execution on irsyad-scoped rows routes through `cc-irsyad-coord`, never
executed directly from this (or any console) body.
