# Joint plan: ONE system for substrate 2.0 + fleet memory + second brain

Author: cc-paperclip-spike-1. Requested by: orch-console (bus #78481, Musa
op#34914), folding in op#34901 (bus #78473), op#34906 (bus #78476),
op#34910 (bus #78479). Gate: orch-console's review, then relay to Musa for
go/no-go. Committed delivery date (my own, bus #78506): 2026-10-28 16:00
UTC. DESIGN ONLY — nothing in this document is built. Every phase below
needs its own TDD'd migration/script and its own small PR, per this
fleet's standing practice (see PR#490/#551/#553/#554 for the shape: tests
first, real CI watched to completion, head re-checked immediately before
merge).

## 0. The target state (Musa's words, via #78481)

1. ONE place of record — the substrate DB: rules/floors, decisions,
   commitments, asks, work-in-progress, and fleet memory as typed rows
   with history.
2. Files become GENERATED VIEWS of the DB (governance-floors.md, the
   data-store registry, console's handoff, STATUS.md), never hand-edited
   sources, so they cannot drift.
3. Fleet memory is the same store, with the same anti-junk rules (#78479).
4. Musa's vault is the personal layer, fed by the curator, with pointers
   into the substrate.

This plan does not re-litigate that target — it inventories what already
exists against it (ground truth, not the bus-thread account, per #78481's
explicit instruction) and sequences the gap.

## 1. What already exists — verified against the live repo/DB, not assumed

### 1a. Substrate 2.0 / BIG-3 work_item SSOT — LIVE

Migration 114 (`migrations/114_work_item_ssot.sql`) is live and clean on
the real substrate: `work_item` + `work_item_audit` tables, 3 mirror
triggers (asks/bus/commitments → work_item), 7 supporting
functions/constraints/indexes. 3905 `work_item` rows, 1850 backfill audit
rows. `scripts/work_item_equivalence_proof.py` (built today, 2026-10-27)
proved row-for-row parity at 19:21Z: asks 468/468, bus 1213/1213,
commitments 145/145, zero divergence. `scripts/work_item_reconcile.py` is
built (drop-invariant classes MISSING/STALE/SOURCE_GONE/OUT_OF_SCOPE/
UNKNOWN_SRC/ABANDONED; dry-run default, `--repair`, `--page`), 33 tests
passing across both scripts.

Primary source: branch `fable/substrate2-build-20261027`@`fcc7826` (draft
PR#548), report `reports/fable-20261027/substrate2-build.md`, cutover doc
`docs/substrate/work_item_cutover_draft.md`.

**This was built TODAY and corrects an earlier claim** (bus #78481 said
cc-substrate "has been idle since 10-24" and this tooling "was never
built" — that's now stale; today's work supersedes it).

**Scope, by that doc's own words**: "BIG-3 only... everything beyond —
fleet memory, second brain, generated views, governance-floor audited
change path — is owned by cc-paperclip-spike-1's joint plan (#78481/
#78476) and is deliberately not restated here." So sections 2-4 below are
this plan's job, not a restatement of the cutover doc's.

**Gaps found in migration 114 (noted, not yet fixed — relevant because
this plan reuses 114's pattern and must not repeat its gaps):**
- no `AFTER DELETE` trigger on a source table → an orphaned mirror row
- `requires_response` true→false on a source row can strand a live
  `work_item` row (no re-sync path)
- `work_item_audit` is append-only **by convention only** — no REVOKE
  stops a direct UPDATE/DELETE on it
- no console read-grant/RLS on `work_item` yet

**The original ask (#4494, "kill the 7-log sprawl") — only partially
enumerated.** I could not find an explicit list of all 7 logs anywhere in
the repo or bus history; cc-substrate's own plan (bus #73176) narrowed
scope to the tractable BIG-3 (asks/bus/commitments) without ever
restating the other 4. **I'm flagging this as a real, unresolved gap
rather than guessing names for it.** Phase 5 below depends on someone
(not necessarily me) re-deriving that list from the original ask.

**247 ABANDONED rows** — the reconciler's first live dry-run at a 72h-
no-activity floor: 246 `operator_asks` (delegated, no due date, untouched
since the 10-23/24 backfill; top owners cc-cosem-platform-4 ×56,
cc-cosem-adcda-1 ×42, orch-console ×25, cc-fleet-health ×22,
cc-cosem-adcda-2 ×16, ...) + 1 `agent_messages` row (#7660, cc-infra).
Console has already ruled on this (bus #78827): the 72h floor stays
dry-run, the first run seeds page-state so only *new* abandonment pages
going forward, and the 247 go to owners as one triage list. This plan
reuses that exact ruling as the first production case of phase 1's
floor-row mechanism (section 2, below) rather than treating it as a
one-off script run.

**NOVEL IDEAS already proposed in the substrate2-build report** (I'm
reusing #1 and #2 directly below, not reinventing them):
1. **Floors-as-executable-rows**: a `floor(id, sentence, op_ref,
   predicate_sql, enforced_in_code)` table; a reconcile loop evaluates
   every predicate on a schedule; governance-floors.md becomes a
   generated view with a live "0 violations" column. The 72h-ABANDONED
   check is proposed as the first floor row.
2. **work_item as the dispatch queue**: the autoscaler boots lanes from
   `captured` rows; a lane's heartbeat stamps `work_item.updated_at` for
   its owned rows; "abandoned" becomes "no heartbeat," an exact signal,
   not a source-edit heuristic.
3. Backtesting a floor via `work_item_as_of(ts)` before ratifying its
   threshold.
4. Tenant-tagged `work_item` as a live client-facing commitments page.
5. Self-proving migrations (`apply_migration.py --proof <module>`
   required for view/trigger-touching migrations).

### 1b. Fleet memory — DESIGNED + partially built (my own op#34635 work)

Shape approved by console (bus #77578/#77584): option 1 (new
`fleet_memory_embeddings` substrate table, pgvector, files stay SSOT,
NO chunk_text stored — only lane/file_path/chunk_offset/embedding/
updated_at) + option 3 as a parallel stopgap + one-way fleet→vault
projection, then vault→fleet inbox ingestion built last.

**Built so far**: `scripts/memory_recall.py` (pure-stdlib BM25 stopgap,
10 TDD tests, smoke-tested), `memory_index_audit.py` extended with
`--audit-frontmatter`/`--fix-frontmatter` (PR#518, 13 new tests, 47/47
total), a fleet-wide frontmatter schema-lint survey (2197 files, 393
(18%) with ≥1 violation across 16 lanes — report only, no fleet-wide fix
applied yet, pending console's review of the per-lane diff).

**Previously HELD, now partially unblocked**: the original design assumed
a Mac Studio BGE-M3 embedding endpoint (offline 55 days) and a vault
location that turned out to be wrong. Corrected (bus #78247): the real
vault, `Musa-Second-Brain`, is already local to the Mini via iCloud, so
the cross-host embedding dependency is **moot** — pgvector + a *local*
embedding call becomes viable without that endpoint. The migration HOLD
from bus #77584 is lifted; a plan (this document, section 3) is owed, not
a build yet — the brainstorming hard-gate still applies to any actual
vault-sync code.

### 1c. Second brain / vault — MOSTLY BUILT, blocked on FDA + a dead lane

The vault's own write pipeline is far more built than the bus-thread
account suggested. `~/wingmen/projects/second-brain/_meta/` (a separate
project directory, NOT this repo) already has: `deploy_to_vault.py`
(the one sanctioned write path — generated/freshness-marked files only,
refuses `00-NORTH-STAR.md`/`04-Deen`/`05-Nasab`/`07-Personal`),
`gen_db_projections.py`, `gen_firehose.py`, `lint_propose.py`,
`inbox_intake.py`, `capture_from_bus.py`, `redact.py`, `consolidate.py`,
`ingest_docs.py`, `ingest_reports.py` — each with its own test file.

**Confirmed blocker (bus #78476 point 7, verified directly)**:
`deploy_to_vault.py` requires macOS Full Disk Access to write iCloud
Mobile Documents paths; a launchd job has none, so only an interactive
terminal that already holds the grant can run `--deploy`. **cc-second-
brain is currently DOWN** — zero rows in `agent_status`, not even stale;
last activity thread #77300 (10-26) says a nightly-regen commit landed
(`760429f`) but the actual `--deploy`/`--check`/`inbox_intake.py`/
`lint_propose.py` run ("Option B", orch #55014) was never executed.

**#78476's actual ask (op#34906)** is broader than read-only recall: Musa
wants the fleet to *populate* the vault from conversations — people,
ideas, partnerships, research, decisions extracted from
`operator_messages` across his channels plus research deliverables —
typed notes that grow/link, a generated daily note, an explicit "remember
this" capture, "what did I say about X" recall, a weekly digest.
Personal-life notes stay vault-only, never mirrored into the shared
substrate beyond what `operator_messages` already holds; embeddings stay
local, never shared.

**#78479's anti-junk + two-layer rules (op#34910)**: one note per entity,
updated in place (dedup, never a new note per mention); a capture bar
(recall-worthy/actionable/decision only — no chit-chat, status-pings, or
fleet noise); a fixed folder shape (People/Ideas/Partnerships/Research/
Decisions/Personal/Daily); 90-day-untouched → keep-or-archive via the
weekly digest, archive never delete; two layers — vault holds the
personal narrative, the fleet-memory DB holds the work layer, each real
thing is written ONCE with the other side holding a pointer, one search
spans both; the same anti-junk rules apply to console's own 330+ memory
files.

### 1d. Governance floors / generated views — ONE real precedent, with a gap

`docs/governance-floors.md` is **100% hand-authored today** — it
explicitly states "THIS FILE IS AUTHORITATIVE," no generated markers.
`docs/data-store-registry.md` **does** have a real, working generated
block (`<!-- GENERATED:data_provenance:START/END -->`, lines 34-50),
produced by `scripts/gen_data_store_registry.py` from the
`data_provenance` table (migration 089). This is the "already a working,
shipped example" referenced in #78481 — real, but:

- **Invocation is manual only.** No launchd plist, no CI step runs it
  (grepped `launchd/`, `.github/`, `Makefile` — zero hits). A file can go
  stale between manual runs, defeating the no-drift goal.
- **No audit trail.** `data_provenance` has `owner`/`updated_at`/
  `created_by` columns but is a plain current-value table: an UPDATE
  overwrites in place, with no history/append-only table behind it. The
  one existing generated-view precedent does **not** already solve the
  "who changed what, when, citing the op#" requirement #78481 asks for —
  that's a real gap, not a detail to inherit for free.

## 2. The design — reusing what's proven, not inventing a second mechanism

**One generation mechanism, reused everywhere.** `gen_data_store_registry.py`'s
pattern (read a DB table → render a fenced block → diff-or-write the
target file) is the template for every "file becomes a generated view" in
target state #2 — governance-floors.md, a future STATUS.md, console's
handoff file. No new rendering mechanism gets invented.

**One audit mechanism, reused everywhere.** Migration 114's
`work_item_audit` is the right shape (append-only history row per change)
but has a real gap: it's append-only **by convention**, not by grant. The
new `governance_floor_audit` table (and any future table needing this)
must close that gap instead of repeating it: `REVOKE UPDATE, DELETE ON
governance_floor_audit FROM PUBLIC` (or no non-owner role gets those
grants at all), insert-only via a trigger or RPC, every row citing the
acting agent and an `op_ref` (NOT NULL — a floor change with no op# to
cite is rejected at the DB layer, not just linted).

**Floors as executable rows (reusing NOVEL IDEA #1 verbatim).** A
`governance_floor(id, sentence, op_ref, predicate_sql, enforced_in_code,
created_at)` table. A small reconciler (same shape as
`work_item_reconcile.py` — dry-run default, `--repair`) evaluates every
`predicate_sql` on a schedule and records pass/fail. governed-floors.md
regenerates from this table via the shared generation mechanism, with a
live "N/M floors passing" line. The 72h-ABANDONED check (already proven
today, 247-row dry-run) becomes the first row.

**Fleet memory stays the approved op#34635 shape** —
`fleet_memory_embeddings` (no chunk_text, just pointers+vectors), files
remain SSOT, BM25 stopgap already live. The vault is now local (iCloud),
so the previously-blocking cross-host embedding dependency is gone; a
local embedding call (same machine, no Tailscale hop) replaces the Mac
Studio endpoint.

**The vault stays the personal layer, fed by a curator, per #78479's
two-layer rule** — not rebuilt from scratch. The existing `_meta/`
pipeline (`gen_db_projections.py`, `capture_from_bus.py`, `inbox_intake.py`,
`lint_propose.py`, `redact.py`) is reused as-is; the blocker is entirely
operational (an FDA-holding runner), not a design gap, so this plan does
not propose new vault-write code.

## 3. Phased sequence, with dates and exit tests

**Phase 0 — DONE.** BIG-3 work_item SSOT (migration 114). Exit test:
equivalence proof, already passed 2026-10-27 19:21Z. No action from this
plan.

**Phase 1 — governance floors as generated, audited rows.**
Scope: `governance_floor` + `governance_floor_audit` tables (append-only
by grant, not convention), the predicate reconciler, regenerating
governance-floors.md via the shared mechanism, the 72h-ABANDONED check as
floor #1 (reusing console's existing ruling on dry-run + page-state +
one-time 247-row triage list — no new policy decision needed).
Exit test: (a) an attempted floor-row change with no `op_ref` is rejected
by the DB, proven by a planted-bad-insert test (same shape as the writer-
gate's planted-INSERT tests); (b) the regenerated governance-floors.md,
diffed against today's hand-authored version, matches it semantically
(one manual review, since today's version is prose, not rows, so no
automatic byte-diff is meaningful on the first run).
Owner: cc-paperclip-spike-1. Target: first PR within 1 week of this plan
gating (own TDD'd migration + reconciler script + tests, own small PR,
own CI watch, same shape as PR#553/#554).

**Phase 2 — PR-B (writer-gate migration 124) — already in flight,
unchanged.** Not re-dated by this plan; console's own ruling stands
("no deadline, quality over speed"). Continues after this plan ships.

**Phase 3 — fleet-memory semantic recall (op#34635 option 1).**
Scope: the `fleet_memory_embeddings` migration + `memory_recall.py`'s
pgvector backend, now that the vault's locality removes the embedding-
endpoint blocker. Exit test: on a labeled query set (to be built from
this lane's own ~30 memory files plus console's), pgvector recall must
(a) not regress precision versus the existing BM25 stopgap's 10 TDD
cases, and (b) surface at least N semantically-related-but-keyword-missed
cases that BM25 misses today (N fixed once the labeled set exists).
Owner: cc-paperclip-spike-1. Target: starts after phase 1 lands (reuses
phase 1's generation+audit pattern for the fleet→vault one-way
projection), estimate 1 week after phase 1's PR merges.

**Phase 4 — second-brain vault pipeline resumption.** Not this plan's
build — the blocker is a dead lane + a human-held OS permission, not a
missing design. This plan's job is to state the blocker precisely (done,
section 1c) and hand Musa a decision: either revive the cc-second-brain
session, or decide who else inherits the FDA-holding interactive-terminal
role — the vault's one-pen rule means it must stay ONE sanctioned runner,
not a parallel path. No date committed here; it is Musa's call, not a
schedulable engineering task.

**Phase 5 — work_item scope beyond BIG-3 (the unenumerated rest of the 7
logs).** Blocked on someone re-deriving the original #4494 list; I found
no record of what the other 4 logs are. Not scheduled until that's
resolved — flagged as an open item, not silently dropped and not guessed
at here.

## 4. Open risks / what this plan does not promise

- The exact "7 logs" from ask #4494 cannot be enumerated from any record
  I found. Phase 5 has no date because of this, not because of neglect.
- The 247 ABANDONED rows need owners to actually act on the triage list;
  this plan reuses console's ruling on how to deliver it but does not
  itself resolve the backlog.
- Second-brain resumption depends on a human decision (Musa) about who
  holds the FDA grant; no engineering date exists for it.
- Migration 114's four noted gaps (no DELETE trigger, no re-sync on
  requires_response flip, convention-only audit table, no RLS) are
  **not** fixed by this plan — they belong to whoever owns 114's own
  follow-up, flagged here so they aren't lost, not claimed as mine.

## 5. Ownership

| Phase | Owner |
|---|---|
| 0 (BIG-3, done) | cc-substrate (complete) |
| 1 (governance floors) | cc-paperclip-spike-1 |
| 2 (PR-B, in flight) | cc-paperclip-spike-1 |
| 3 (fleet memory) | cc-paperclip-spike-1 |
| 4 (second brain resumption) | cc-second-brain + Musa's decision |
| 5 (work_item scope expansion) | unassigned — needs the #4494 list first |
