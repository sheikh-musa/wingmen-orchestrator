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

**Revision 1 (console's tightening pass, bus #78846, approved-with-5-
changes): dated targets instead of durations (weekdays verified against
`date -d`), migration 114's 4 gaps now explicitly owned by this lane
(cc-substrate retired), a safer governance-floors.md first step (a
generated block inside the file, not a full regen), Phase 5 unblocked
using Musa's own ruling (operator_messages 32731/32732) instead of a
guessed list, Phase 4 reframed as one plain decision for Musa. All 5
folded in below.**

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

**Gaps found in migration 114 — now owned by this lane (cc-substrate
retired 2026-10-27; console's ruling, bus #78846 item 2), scheduled as
Phase 1b below:**
- no `AFTER DELETE` trigger on a source table → an orphaned mirror row
- `requires_response` true→false on a source row can strand a live
  `work_item` row (no re-sync path)
- `work_item_audit` is append-only **by convention only** — no REVOKE
  stops a direct UPDATE/DELETE on it (fixed inside Phase 1a, same
  migration as the new governance-floor audit table — identical pattern)
- no console read-grant/RLS on `work_item` yet

**The original ask (#4494, "kill the 7-log sprawl") — defined, not
unenumerated.** Console's own proposal to Musa (operator_messages id
32731, 2026-10-21) names the full scope: every ask, bus task, commitment,
build, and operator message. Musa ruled "big 3 first" the same day
(operator_messages id 32732) — asks/bus/commitments are Phase 0 (done);
build and operator-message-shaped items, plus whatever else a live-schema
inventory surfaces, are Phase 5 below, not an unresolved mystery.

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
cite is rejected at the DB layer, not just linted). Per console's
tightening (bus #78846 item 2), `work_item_audit` itself gets the same
REVOKE fix inside Phase 1 (one shared migration, same pattern, same PR) —
not deferred, since it's the identical change applied to a second table.

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

All dates verified weekday-against-date with `date -d` before writing
them (the nazim_send weekday guard exists because this fleet got this
wrong before — not repeating it here).

**Phase 0 — DONE.** BIG-3 work_item SSOT (migration 114). Exit test:
equivalence proof, already passed 2026-10-27 19:21Z. No action from this
plan.

**Phase 1a — governance floors: a generated block inside the existing
file, not a full regen.**
Per console's tightening (bus #78846 item 3): governance-floors.md
carries rulings in hand-authored prose that rows cannot yet express, so
Phase 1 does NOT regenerate the whole file. Instead: a `governance_floor`
+ `governance_floor_audit` table pair (append-only by grant — `REVOKE
UPDATE, DELETE ... FROM PUBLIC`, insert-only via trigger/RPC, every row
citing the acting agent and a NOT NULL `op_ref`), a small reconciler
(same shape as `work_item_reconcile.py`), and ONE generated FENCED BLOCK
inserted into governance-floors.md (reusing `gen_data_store_registry.py`'s
exact block-insertion mechanism, the same `<!-- GENERATED:...:START/END
-->` pattern) holding the floors table + a live "N of M floors passing"
line. The surrounding prose stays hand-authored. Full-file regeneration
is deferred until the rows can provably express every floor currently in
prose — not attempted in Phase 1a. The 72h-ABANDONED check (already
proven, 247-row dry-run, console's ruling in bus #78827: stays dry-run,
seeds page-state so only new abandonment pages, the 247 go to owners as
one triage list) becomes floor row #1. The same REVOKE fix also closes
`work_item_audit`'s convention-only gap in this same migration (console's
tightening item 2 — identical pattern, one shared PR).
Exit test: (a) an attempted floor-row or work_item_audit change with no
`op_ref`/outside the insert-only path is rejected by the DB, proven by a
planted-bad-write test (same shape as the writer-gate's planted-INSERT
tests); (b) the fenced block renders correctly inside governance-floors.md
with the prose around it byte-identical to before.
Owner: cc-paperclip-spike-1. **First PR by Tue 3 Nov 2026. Exit test
passed by Fri 6 Nov 2026.**

**Phase 1b — migration 114's 4 gaps, now owned by this lane.**
Per console's tightening (bus #78846 item 2): cc-substrate was retired
tonight (2026-10-27), so these are explicitly mine now, not an
unclaimed follow-up:
1. An `AFTER DELETE` trigger on each BIG-3 source table, so a deleted
   source row doesn't orphan its `work_item` mirror. Test: delete a
   source row, assert the mirror is marked dropped (not left dangling).
2. Re-sync when `requires_response` flips true→false on a source row
   after its `work_item` mirror was created. Test: flip it, assert the
   mirror's state updates, not just the source.
3. RLS on `work_item` and `work_item_audit` (console currently has no
   read-grant). Test: a non-console/non-superuser role's `SELECT` is
   denied or scoped per whatever policy this PR defines; console's own
   role can read.
Each gets its own TDD'd test, same migration/PR as above (Phase 1a and
1b ship as one coherent PR — same table family, same reviewer context).
Owner: cc-paperclip-spike-1. **Folded into the same PR and dates as
Phase 1a (first PR by Tue 3 Nov, exit tests passed by Fri 6 Nov 2026).**

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
Owner: cc-paperclip-spike-1. **Started by Mon 9 Nov 2026. Exit test
passed by Mon 16 Nov 2026.**

**Phase 4 — second-brain vault pipeline resumption: one plain decision
for Musa.**
Not this plan's build — the pipeline itself is mostly already built
(section 1c); the blocker is a dead lane plus a macOS permission that
only an already-granted interactive terminal holds. Framed plainly, per
console's tightening (item 5): **Musa needs to choose one of two
things — restart the cc-second-brain session on the Mini, or say who
else should hold that Mac's "Full Disk Access" permission so they can
run the vault-sync scripts.** No date committed here; it is Musa's
decision, not a schedulable engineering task.

**Phase 5 — work_item scope beyond BIG-3: a dated 1-day inventory, not a
guess.**
Per console's tightening (bus #78846 item 4): this is NOT blocked on an
unenumerated old list. Console's own proposal to Musa (operator_messages
id 32731, 2026-10-21) already defines the full scope — every ask, bus
task, commitment, build, and operator message — and Musa ruled "big 3
first" (operator_messages id 32732, same day). Scope: a 1-day task that
queries the live schema for every table holding an item owed to someone
(an owner/status/state-shaped column) and proposes the cutover order.
A first pass of that query (run during this plan's drafting, not a final
answer) already found 37 tables with an `owner`/`status`/`state`/
`assignee`/`owned_by`/`assigned_to` column, including clear "build"-
shaped candidates (`jobs`, `jobs_archive`, `change_requests`,
`qa_findings`, `bug_reports`, `bug_pipeline_readiness`) and one real
surprise worth flagging now rather than losing: `exec_work_items` already
has its own separate `state` column — a possible SECOND un-folded
fragmenter alongside the original BIG-3, not yet accounted for anywhere.
A real caveat for whoever runs the actual inventory: `operator_messages`
itself (one of the 5 named categories) has NO owner/status/state column
at all — it uses `handled_at`/`deferred_at`/`cos_triage` instead, so a
naive owner/status column grep misses it; the real inventory task needs
a second detection pass for that shape, not just the column-name query
used here as a first pass. **Dated after Phase 1** — no fixed date yet
since it depends on Phase 1a/1b's PR landing first; console/Musa to
confirm a date once Phase 1 ships.

## 4. Open risks / what this plan does not promise

- The 247 ABANDONED rows need owners to actually act on the triage list;
  this plan reuses console's ruling on how to deliver it but does not
  itself resolve the backlog.
- Second-brain resumption depends on Musa's decision about who holds the
  FDA grant; no engineering date exists for it (Phase 4).
- Phase 5's 37-table first-pass is a STARTING POINT for the 1-day
  inventory task, not its answer — most of those 37 are plausibly NOT
  "items owed to someone" in the asks/commitments sense (e.g. `payments`,
  `sites`, `receipts`, `revenue_ledger` are operational/financial state).
  Real triage judgment is still owed, which is exactly why console scoped
  it as a dedicated task rather than something to auto-execute here.

## 5. Ownership

| Phase | Owner | Target date |
|---|---|---|
| 0 (BIG-3, done) | cc-substrate (complete) | done 2026-10-27 |
| 1a (governance floors, generated block) | cc-paperclip-spike-1 | PR by Tue 3 Nov 2026; exit test by Fri 6 Nov 2026 |
| 1b (migration 114's 4 gaps) | cc-paperclip-spike-1 | same PR/dates as 1a |
| 2 (PR-B, in flight) | cc-paperclip-spike-1 | no date (console's standing ruling) |
| 3 (fleet memory) | cc-paperclip-spike-1 | started Mon 9 Nov 2026; exit test by Mon 16 Nov 2026 |
| 4 (second brain resumption) | Musa (one decision) + cc-second-brain | no engineering date — Musa's call |
| 5 (work_item scope inventory) | unassigned — needs a runner | dated after Phase 1 lands |
