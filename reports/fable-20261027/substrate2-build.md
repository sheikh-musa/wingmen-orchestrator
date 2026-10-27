# Substrate 2.0 builder run — work_item SSOT reconcile + equivalence proof + cutover draft

- Lane: cc-substrate-1 (temporary Fable 5.1 lane, irsyad pool), booted by orch-console on Musa op#34969-34972; task bus #78603; ask 4494.
- Branch: `fable/substrate2-build-20261027` (draft PR against `fable/substrate-safe-fixes`, NOT for merge).
- Store examined: orchestrator substrate `tscuymavysscrvoberrr` (data layer), read-only. No DDL, no migration, no writes to `work_item` or any source table. The only substrate writes this run made: `read_at` stamps on my own two inbox rows (#78603, #77832) and bus rows via `scripts/bus_send.py`. Nothing touched the ihsanos multi-tenant DB, any client silo, or ADCDA prod.
- Run window: 19:10Z–20:50Z on 2026-10-27.

## Ranked findings

### 1. Migration 114 is live and CLEAN — the equivalence proof PASSES row-for-row (19:21Z)

Verified read-only against the live store:

| check | result |
|---|---|
| tables `work_item`, `work_item_audit` | present |
| triggers `work_item_mirror_{ask,bus,commitment}_trg` | all 3 present, enabled (`tgenabled='O'`) |
| functions `work_item_derive_{ask,bus,commitment}`, `work_item_mirror`, 3 trigger fns | all 7 present |
| constraints (kind/state CHECKs, done/dropped attributable, owned-needs-owner, source UNIQUE, pkey) | all 7 present |
| indexes (open_activity, owner, due partials + pkeys + source_uk + audit_item) | all 7 present |
| `work_item` rows | 3905 (first 2026-10-23 00:29Z; still moving at 19:12Z) |
| backfill audit rows | 1850 `mirrored` by `backfill:114_work_item_ssot` (plan said 1809; the delta is rows that opened between plan and apply) |

Proof (`scripts/work_item_equivalence_proof.py`, artifact `work_item_equivalence_proof_20261027.md` beside this file):

| kind | open source | live work_item | open w/o live | live w/o open | wi rows | distinct sources |
|---|---|---|---|---|---|---|
| ask | 468 | 468 | 0 | 0 | 1163 | 1163 |
| bus | 1213 | 1213 | 0 | 0 | 2520 | 2520 |
| commitment | 145 | 145 | 0 | 0 | 226 | 226 |

(P3) state/owner equality against the migration's own `work_item_derive_*()` functions: 0 divergent rows. Structural classes (MISSING, STALE, SOURCE_GONE, OUT_OF_SCOPE, UNKNOWN_SRC): 0.

Two definitional deltas are explained, not failures, and the proof reports them every run:
- 5 `operator_asks` rows have `triage_state='not_an_ask'` with `closed_at` NULL. A naive "closed_at IS NULL = open" count calls them open; `work_item` correctly holds them as `dropped`; `asks_reconcile_daily`'s CLOSE step is the legacy repair for exactly those rows.
- 26 `agent_messages` rows are requires_response + unresponded + `skipped_at` set. `reconcile_responded_at`'s raw filter includes them; `work_item` holds them as `dropped`.

### 2. The drop-invariant watchdog's first live run finds 247 ABANDONED rows (decision needed)

`scripts/work_item_reconcile.py` dry-run (read-only, artifact `work_item_reconcile_dryrun_20261027.json`), 72h activity floor, rows with a future `due_at` excluded as "parked":

| kind | state | count | note |
|---|---|---|---|
| ask | owned | 246 | delegated, no due date, no source activity since the 10-23/24 backfill |
| bus | owned | 1 | agent_messages#7660, claimed by cc-infra, untouched 115h |

Top owners of the 246 asks: cc-cosem-platform-4 (56), cc-cosem-adcda-1 (42), orch-console (25), cc-fleet-health (22), cc-cosem-adcda-2 (16), cc-cosem-platform-1 (13), cc-cosem-exams-1 (11), cc-substrate-1 (9), cc-bariq-1 (8), cc-irsyad-coord (6).

Before excluding future-due rows the count was 292 (40 in_progress + 4 owned commitments due in the future, 1 ask). At a 7-day floor the count is 0 today, only because the table is 5 days old.

What orch-console needs to ratify (item (d) of #73179): the floor (72h proposed) and the first-run policy. The script pages ONE bus row per run (P1, requires_response, de-duplicated per row for 24h), so the first page would list 247 rows. Options: accept one big page; or seed the page-state file so the backlog is acknowledged and only NEW abandonment pages. The `updated_at` signal is "source row last written", which is the right signal for drift but is naturally quiet for an ask that was correctly delegated and is being worked outside the ledger; the soak in cutover phase (d) measures that.

### 3. Built this run (all tested on ephemeral PG17, never against the substrate)

- `scripts/work_item_reconcile.py` — ONE reconciliation across the BIG-3. Classes: MISSING, STALE, SOURCE_GONE, OUT_OF_SCOPE, UNKNOWN_SRC, ABANDONED. Dry-run by default (exit 0 clean / 2 divergent; `--json`). `--repair` re-mirrors MISSING/STALE through the migration's own `work_item_mirror()` + `work_item_derive_*()` (one definition, no drift between trigger, backfill, proof and repair) and drops live SOURCE_GONE/OUT_OF_SCOPE rows with an attributable reason + audit row; ABANDONED is never auto-repaired. `--page` posts one bus row per run via `bus_send.py` (P1, requires_response). Actor tag `reconcile:work_item_reconcile` follows the migration's provenance convention; `is_agent_actor()` treats `source:*`/`reconcile:*`/`trigger:*`/`backfill:*` as non-agents.
- `scripts/work_item_equivalence_proof.py` — (P1) every OPEN source row has exactly one LIVE work_item row, (P2) and vice versa, (P3) state/owner equal the derive functions. Canonical "open" per kind is each legacy sweep's own notion (header documents it). Writes a markdown artifact (`--out`), exit 0/1.
- `tests/migrations/test_work_item_reconcile.py` — 13 tests: clean baseline; closed-historical-no-mirror is not a finding; each class detected → repaired → proof flips FAIL→PASS; abandoned pages once and re-pages after the window; future due date is parked; captured rows never abandoned; legacy deltas explained; CLI exit codes and dry-run writes nothing. Plus the 18 existing migration 114 tests still pass (33 total).
- `docs/substrate/work_item_cutover_draft.md` — the cutover sequence (finding 4).

### 4. Cutover sequence drafted (BIG-3 readers only; the joint plan #78481 owns everything beyond)

Preconditions: reconcile scheduled under launchd every 10 min (dry-run + page), proof clean for 72h, artifact committed. Then one reader at a time, each reversible with the old sweep loaded but unscheduled until soak + orch-console re-gate: (a) `asks_daily_digest.py` read path, (b) `commitment_sweeper` fire logic, (c) `reconcile_responded_at` match/stamp, (d) the cross-kind drop-invariant watchdog replaces the three per-log stale checks, (e) retire the old crons one at a time. `dev.wingmen.asks-reconcile` is NOT retired by this plan because its CLOSE step is a source-side writer.

### 5. Gaps migration 114 leaves open (detected now; fixes proposed, no DDL written this run)

| gap | effect | detected by | proposed fix (follow-up migration, number unassigned) |
|---|---|---|---|
| no DELETE trigger on the sources | a deleted source row leaves a live work_item row | SOURCE_GONE | AFTER DELETE trigger → dropped with reason `source row deleted` |
| `requires_response` true→false after mirror | trigger's WHEN clause stops firing; live row never closes | OUT_OF_SCOPE | trigger WHEN on `OLD.requires_response OR NEW.requires_response`, derive → dropped |
| `work_item_audit` append-only by convention | an UPDATE/DELETE on audit is possible | — | REVOKE UPDATE, DELETE on `work_item_audit` from writers |
| no console read grant / RLS on `work_item` | the console cannot read the SSOT it will gate on | — | GRANT + `work_item_console_ro` policy, both (a GRANT without a policy is deny-all) |

## Not done / out of scope this run

- No launchd plist for the scheduled reconcile (deploy-side; the cutover's precondition 1). Trivial to add once the floor is ratified.
- The three legacy sweeps' logic was NOT ported into `work_item_reconcile.py` (plan #73176 item 4's full scope). This run's reconcile is the drift backstop + drop-invariant watchdog that gate #73195 named; the ports are cutover phases (b) and (c).
- No migration, no repair run, no page sent. `--repair`/`--page` exist and are tested, but running them against the substrate needs an orch-console gate.

## Verification record

- `pytest tests/migrations/test_work_item_reconcile.py tests/migrations/test_114_work_item_ssot.py` → 33 passed (run with DATABASE_URL unset; the conftest guard refuses the production DSN).
- `scripts/work_item_equivalence_proof.py --out …` → PASS, exit 0 (live, read-only, 19:21Z).
- `scripts/work_item_reconcile.py --json` → `{'ABANDONED': 247}`, exit 2 (live, read-only, 19:24Z), no repair, no page.

## NOVEL IDEAS (Musa op#34981-82, from the substrate 2.0 vantage point)

### 1. Floors as executable rows: every governance floor is a SQL predicate that evaluates itself
Today `docs/governance-floors.md` is prose that agents promise to remember. Migration 114 already shows the alternative: the state truth table IS the `work_item_derive_*()` function, so the doc and the code cannot drift. Generalize it: a `floor` table where each row carries a human sentence, the op# that ratified it, and a SQL predicate that must return zero rows. The reconcile loop evaluates every predicate every 10 minutes and pages on the first violating row, and the floors doc becomes a generated view of that table with a live "0 violations, checked 3 min ago" column per floor.
- Feasible for us: the substrate, the reconcile loop, the page path and the audited-change pattern all exist now; the first floor row is the ABANDONED check built this run.
- First step: a follow-up migration with `floor(id, sentence, op_ref, predicate_sql, enforced_in_code bool)` plus 5 rows ported from the floors doc; the reconcile script gains a `floors` pass.
- Unlocks: a floor that cannot be written as a predicate is flagged as "promise, not code" at ratification time, and the joint one-system plan (#78481) gets its binding-rules table for free.

### 2. work_item becomes the fleet's dispatch queue, and liveness comes from heartbeats, not source edits
The 247 ABANDONED rows show the real gap: the ledger says "owned" while the owner's activity happens outside it. Flip the direction: the autoscaler boots lanes FROM `captured` rows, a lane claims a row by compare-and-swap, and its existing heartbeat touches `updated_at` on every row it owns. Abandonment then means "no heartbeat", which is exact, and lane count follows open work instead of a fixed roster.
- Feasible for us: the autoscaler, the lane heartbeat and the work_item partial indexes already exist; this is one UPDATE added to the heartbeat and one SELECT added to the autoscaler.
- First step: heartbeat stamps `work_item.updated_at` for rows where `owner_agent` is this lane; re-run the dry-run reconcile after 24h and compare the ABANDONED count.
- Unlocks: token spend proportional to open work (Max-plan singletons, builder lanes scaled to the backlog), and the drop invariant becomes a liveness guarantee rather than a heuristic.

### 3. Backtest a floor before ratifying it: `work_item_as_of(ts)` over the audit trail
`work_item_audit` records every transition, so the open-work state at any past instant is reconstructible. A single SQL function `work_item_as_of(ts)` plus a backtest script answers "a 72h floor would have paged N rows last week, M of which were real drops" before orch-console ratifies a number. The same function gives Musa a mechanically true daily "what moved, what did not" for the second-brain daily note (#78476).
- Feasible for us: the audit table exists with 5 days of history already; the function is a window query, no new storage.
- First step: write the function and backtest the ABANDONED floor at 48h, 72h and 7d over 10-23 to 10-27, attach the table to the floor-ratification request.
- Unlocks: evidence-based floor tuning, and an honest "what happened today" feed that no agent can embellish.

### 4. The ledger as a client-facing product: a live, signed commitments page per tenant
`work_item` already holds every open commitment with owner, age and due date. Tag each row with its tenant (derived from the ask's channel) and render a read-only page per client on share.wingmen.dev: "here is everything Wingmen owes you right now, who holds it, and when it is due." The fleet's audit discipline becomes the thing clients buy, and the substrate-as-product roadmap gets its first external surface.
- Feasible for us: source_ref and channel tags exist, share.wingmen.dev exists, and the console-ro GRANT + RLS pattern is the only new DB work.
- First step: a `tenant` column on work_item derived at mirror time, a `work_item_tenant_ro` view, one static render for one consenting client (irsyad, with Shuq and Wan as the sign-off per the floors SSOT).
- Unlocks: a sellable always-on ops desk with a live ledger, and residency discipline by construction, since a tenant tag forces the silo check on every row.

### 5. Self-proving migrations: "applied" means "proven"
Decision 962 happened because a tool could silently strip view arms and nothing checked afterwards. Make `apply_migration.py` refuse to record a migration as applied until a paired `proofs/<n>_proof.py` runs clean against the live store immediately after apply (read-only) and its artifact is written into the ledger row. Migration 114 now has exactly such a proof; this makes it the rule instead of the exception.
- Feasible for us: apply_migration already gates on the 12-char sha and writes a ledger row; the proof for 114 is this run's script.
- First step: add `--proof <module>` to apply_migration, require it for any migration that touches a view or trigger, backfill 114's proof as the first paired artifact.
- Unlocks: view and trigger drift of the decision-962 class is caught at apply time, and the gate reviewer reads a proof result instead of a promise.
