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
every agent at boot and before acting.

- `fleet_memory(id, scope, scope_key, fact text, op_ref text NOT NULL,
  created_by, created_at, retired_at, retired_op_ref,
  CHECK (retired_at IS NULL OR retired_op_ref IS NOT NULL))`
  - `scope` ∈ `fleet | client | repo`; `scope_key` is null for fleet-wide,
    else a client slug or repo name. Scoping is columns, not separate tables,
    so one query covers "everything relevant to me."
  - `fleet_memory_audit`: append-only via REVOKE-ALL-then-GRANT-INSERT,
    written by the same `SECURITY DEFINER` trigger shape as migration 131.
  - Staleness/supersession: a new row with the same `(scope, scope_key)` and
    an overlapping subject retires the old one (`retired_at` + `retired_op_ref`
    citing the new row's `op_ref`) rather than deleting it — audit trail stays
    intact, readers only ever see non-retired rows by default.
- Integrates with, does not replace, per-agent memory files: a lane's own
  `MEMORY.md`/memory files stay for personal working notes; `fleet_memory` is
  for facts that must survive a `/clear` and reach OTHER agents. Boot sequence
  gets one more step: `SELECT * FROM fleet_memory WHERE retired_at IS NULL AND
  scope_key IN (<my repos/clients>) OR scope = 'fleet'`.
- CAI-1034: rows store rule TEXT + op# refs only. No client data (names, IDs,
  transactions) ever lands in `fact` or `scope_key` beyond a client *slug*.
- Migration: direct psycopg-apply only, from a worktree PR to
  `fable/substrate-safe-fixes`, console-gated before apply — never
  `supabase db push` against any production store.

## B. Per-repo code map

Generated from code (not hand-maintained prose), refreshed on every merge,
with a staleness alarm.

- Populates the existing but-empty `repo_context.architecture_summary` column
  — no new table needed here, unlike (A).
- Generator: a script run post-merge (git hook or CI step on the repo, not a
  human) that walks the repo's top-level structure + entry points and writes
  a short structured summary (modules, owned tables/services, key entry
  files) — scoped small on purpose, not a full AST dump.
- Staleness alarm: `repo_context` gets a `architecture_summary_updated_at`
  check; `cc-fleet-health`'s existing sweep pattern (it already runs a
  periodic self-heal sweep for sequence drift etc.) adds a check that pages
  if a repo's `architecture_summary_updated_at` is older than its latest
  merged commit.
- Starting repos (smallest blast radius first): `cosem-adcda`,
  `cosem-platform`, `ihsanos`.

## C. Slices, with honest dates

1. **2026-10-31** — `fleet_memory` + `fleet_memory_audit` migration written,
   console-gated, applied direct-psycopg. First rows seeded: the irsyad
   standing client-rules seed list (#80901) as `scope=client, scope_key=irsyad`
   — strongest first-slice candidate since it's already costing coord re-asks
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
