# work_item SSOT — draft reader cutover sequence (BIG-3)

**Status:** DRAFT, not for execution. Written 2026-10-27 by the temporary Fable lane
`cc-substrate-1` under Musa op#34969 (bus #78603), against the greenlit plan for
ask 4494 (bus #73169 / #73176 / #73179 / #73195, migration 114).

**Scope boundary (one line):** this document covers ONLY the three BIG-3 readers/sweeps
(asks digest, commitment sweeper, responded_at reconciler) and the retirement of their
old per-log crons. Everything beyond that — fleet memory, second brain, generated views
of the DB, governance-floor rows with an audited change path — is owned by
cc-paperclip-spike-1's joint one-system plan (bus #78481 / #78476) and is deliberately
not restated here.

Store throughout: orchestrator substrate `tscuymavysscrvoberrr` (LAYER-VOCAB-001).
Gate owner for every phase: **orch-console**. cai is advisory only (op#34916).

---

## 1. Ground truth (read-only, 2026-10-27 19:12Z)

Verified with read-only queries against the live substrate; no writes.

| Check | Result |
|---|---|
| `work_item` rows total | 3905 |
| Triggers `work_item_mirror_{ask,bus,commitment}_trg` | 3 present, all enabled (`tgenabled='O'`) |
| Backfill audit rows (`actor='backfill:114_work_item_ssot'`) | 1850 |
| Open source rows with NO `work_item` row | 0 (asks), 0 (bus RR), 0 (commitments) |
| Live `work_item` rows whose source is closed or missing | 0 |

Open rows per kind match the sources exactly:

| kind | work_item open states | source open count |
|---|---|---|
| ask | 84 captured + 382 owned = 466 | 466 (`closed_at IS NULL AND triage_state <> 'not_an_ask'`) |
| bus | 1216 captured + 1 owned = 1217 | 1217 (`requires_response AND responded_at IS NULL AND skipped_at IS NULL`) |
| commitment | 65 owned + 80 in_progress = 145 | 145 (`status IN ('pending','fired')`) |

**Definitional wrinkle (the only one found):** 5 `operator_asks` rows carry
`triage_state='not_an_ask'` with `closed_at IS NULL`. `work_item` correctly holds them as
`dropped` (per the migration 114 truth table). The naive "`closed_at IS NULL` = open" count
would call them open. `asks_reconcile_daily.py`'s CLOSE step is the legacy repair for
exactly those rows (it stamps `closed_at` on `done`/`not_an_ask` drift). The equivalence
proof must therefore use each source's OWN canonical open predicate (the right-hand column
above), never bare `closed_at IS NULL`.

---

## 2. Preconditions before ANY reader cutover

All three must hold; none is a reader cutover itself.

1. **`scripts/work_item_reconcile.py` scheduled** under launchd, every 10 minutes, in
   dry-run mode (it never writes to the sources or to `work_item`). On any divergence
   class (MISSING, SOURCE_GONE, STALE, OUT_OF_SCOPE, ABANDONED) it pages the
   orch-console bus at P1 via `scripts/bus_send.py`. Detection is ungated; it pages,
   it does not repair.
2. **`scripts/work_item_equivalence_proof.py` runs clean for 72h** against the live store:
   every open source row ↔ exactly one live `work_item` row, per kind, row-for-row
   (ids, not counts), using the canonical predicates in §1.
3. **The proof artifact is committed** under `reports/` (one file per run day, the final
   one stating "72h clean") and cited in the gate request to orch-console.

---

## 3. Phases — one reader at a time, each reversible

Common rules for every phase:

- The old sweep stays **loaded and unscheduled** (plist unloaded via `launchctl bootout`,
  file kept in `launchd/`), never deleted, until its domain's soak is complete AND
  orch-console re-gates the retirement.
- The source table stays the system of record for its own domain; the migration 114
  trigger keeps mirroring. No reader ever writes `work_item` directly.
- Rollback for every phase is a single reversible step: re-point the read (or re-load the
  old plist). No data migration is needed to roll back because nothing is deleted.

### Phase a — `asks_daily_digest.py` read path → `work_item` (kind='ask')

| | |
|---|---|
| What changes | The digest's open-ask query reads `work_item WHERE kind='ask' AND state IN ('captured','owned')` (and `triage_summary` is joined back from `operator_asks` by `source_id`). |
| What stays | `operator_asks` remains the ledger; `asks_triage.py` keeps writing it; `dev.wingmen.asks-reconcile` keeps running (it is a writer, not a reader — not in this phase). |
| Verify | For 7 consecutive digest runs: the set of ask ids the new read returns equals the set the legacy query (`triage_state='ask' AND closed_at IS NULL` ∪ `captured AND closed_at IS NULL`) returns on the same instant. Log both id-sets; diff must be empty. |
| Soak | 7 days. |
| Rollback | Re-point the read to the legacy query (one-line config/flag); no plist change. |
| Gate | orch-console re-gate on the 7-day diff log. |

Lowest blast radius: a text-only summary, already deduped, no state written.

### Phase b — `commitment_sweeper.py` fire logic → `work_item` (kind='commitment')

| | |
|---|---|
| What changes | The "due and not yet fired" scan reads `work_item WHERE kind='commitment' AND state='owned' AND due_at <= now()`; the fire action still UPDATEs `held_commitments.status='fired'` (system of record), and the trigger mirrors it to `in_progress`. |
| What stays | `held_commitments` stays the system of record; fire-once / never-auto-discharge semantics unchanged (CAI-RESP-1029); the ask-deadline commitment bridge (source_ref `operator_asks:<id>`) unchanged. |
| Verify | For each sweep run during soak: the set of commitment ids the `work_item` scan would fire equals the set the legacy `status='pending' AND due_at <= now()` scan would fire. Run both, fire from the legacy one, log the diff; diff must be empty for the whole soak. |
| Soak | 7 days (one full weekly cycle of due dates). |
| Rollback | Flip the read back to `held_commitments`; the write path never moved. |
| Gate | orch-console re-gate on the soak diff log. |

### Phase c — `reconcile_responded_at.py` match/stamp logic → `work_item` (kind='bus')

| | |
|---|---|
| What changes | The candidate scan reads `work_item WHERE kind='bus' AND state IN ('captured','owned')` instead of `agent_messages WHERE requires_response AND responded_at IS NULL`. The 3-tier thread/backref match and the stamp (`UPDATE agent_messages SET responded_at, response_ref`) are unchanged; the trigger mirrors the stamp to `done`. |
| What stays | `agent_messages` stays the system of record; `bus_send.py` and the hub's drain are untouched; the RR-subset scope (bus #73179) is unchanged. |
| Verify | Per run: candidate id-set from `work_item` equals candidate id-set from the legacy predicate (minus `skipped_at IS NOT NULL`, which `work_item` already holds as `dropped`). Diff must be empty; stamped-count per run must match the legacy run on the same instant. |
| Soak | 7 days. |
| Rollback | Re-point the candidate read; the stamp path never moved. |
| Gate | orch-console re-gate. Highest blast radius of the three (it writes `responded_at` fleet-wide), hence last. |

### Phase d — cross-kind drop-invariant watchdog replaces the three per-log stale checks

| | |
|---|---|
| What changes | One check in `work_item_reconcile.py`: any row in `owned` or `in_progress` with `updated_at` older than the activity floor AND no future `due_at` (a row parked against a future date is waiting, not abandoned) is paged as ABANDONED (abandoned mid-state), regardless of kind. One bus row per run, de-duplicated per row for 24h. First-run burst, measured read-only 2026-10-27 19:21Z at a 72h floor: 247 rows (246 delegated asks with no due date, 1 bus); orch-console should ratify the floor AND whether the first page is a digest or a seeded (pre-acknowledged) page-state. This is the reading orch-console confirmed in #73179 (1): done/dropped are already attribution-forced by the CHECK constraints; the real drop risk is the abandoned middle. |
| What stays | The three legacy stale checks (asks STALE/OVERDUE, commitment overdue nudge, RR SLA) keep running until (e). |
| Floor | **Proposed 72h**, to be ratified by orch-console (item (d) of #73179 asked for the value and that it PAGES, fail-loud). Uses the partial index `work_item_open_activity_idx`. |
| Verify | During soak, every ABANDONED page is cross-checked against what the legacy stale checks paged on the same row; any row the legacy checks paged that ABANDONED did not is a floor-tuning finding, not a go. |
| Rollback | Disable the ABANDONED class by flag; the legacy checks are still live. |
| Gate | orch-console ratifies the floor, then re-gates after soak. |

### Phase e — retire each old launchd cron, one at a time

| Cron | Retire after |
|---|---|
| `dev.wingmen.asks-daily-digest` (read moved in a) | Phase a soak + re-gate |
| `dev.wingmen.commitment-sweeper` (read moved in b) | Phase b soak + re-gate |
| `dev.wingmen.reconcile-responded-at` (read moved in c) | Phase c soak + re-gate |
| `dev.wingmen.asks-reconcile` (CLOSE step still repairs source drift) | NOT retired by this plan — its CLOSE step is a source-side writer; its UNTRIAGED/OVERDUE/STALE checks fold into (d) |

Retirement = `launchctl bootout` + a commit that moves the plist under `launchd/retired/`
with the gate bus id in the commit message. Never all three in one gate; never before the
reconcile (§2.1) has run clean across the domain for the whole soak.

---

## 4. Gaps migration 114 leaves open (follow-up migrations, numbers NOT assigned)

| Gap | Effect today | Reconcile class that catches it | Proposed fix (a follow-up migration, gated) |
|---|---|---|---|
| No `AFTER DELETE` trigger on any source | A deleted source row leaves a live `work_item` orphan | SOURCE_GONE | Add `AFTER DELETE` triggers that mark the mirror `dropped` with `dropped_by='source:<table>'`, `drop_reason='source row deleted'` (keeps the drop-invariant; never deletes the mirror). |
| `requires_response` flipped true→false after mirror | The bus trigger's `WHEN (NEW.requires_response)` no longer fires; the live row sits out of scope forever | OUT_OF_SCOPE | Change the trigger WHEN to `(NEW.requires_response OR OLD.requires_response)` and have `work_item_derive_bus` return `dropped` (`drop_reason='left requires_response scope'`) when the new value is false. |
| `work_item_audit` append-only by convention only | A session with table write could UPDATE/DELETE audit rows | (not detectable after the fact) | `REVOKE UPDATE, DELETE ON work_item_audit FROM <all non-owner roles>`; keep INSERT. |
| No RLS / console read grant on `work_item` | Console cannot read the SSOT it is meant to gate on | — | Per the project rule "GRANT without RLS policy = deny-all": GRANT SELECT to the console reader role AND add a `work_item_console_ro` policy, both, verified via the READER DSN. Same pair for `work_item_audit`. |

None of these blocks phases a–c; the first two are detected (and paged) by the reconcile
from §2.1 in the meantime.

---

## 5. Migration discipline (unchanged from #73176 / #73179)

- Pin the store: `tscuymavysscrvoberrr` per `docs/data-store-registry.md`.
- Full DDL to the orch-console gate BEFORE any apply; report the sha prefix.
- Apply via direct psycopg (decision 962 / `scripts/apply_migration.py --gate`), **never**
  `supabase db push`.
- No explicit ids in any migration, backfill or test; read ids back via `RETURNING`.
- Every trigger body stays fail-safe for the source table (#73195): `BEGIN … EXCEPTION
  WHEN OTHERS THEN RAISE WARNING`, never re-raise.

---

## 6. Dates (relative, earliest only)

| Milestone | Earliest |
|---|---|
| Reconcile scheduled + proof running | the day the draft PR is reviewed and merged by orch-console |
| 72h proof soak complete | merge day + 3 |
| Phase a start | the day after the 72h soak reads clean |
| Phase b start | phase a re-gate (≥ a + 7d) |
| Phase c start | phase b re-gate (≥ b + 7d) |
| Phase d floor ratified | can run in parallel with a; ratification at the phase-a gate |
| Phase e retirements | each ≥ 7d after its own phase's re-gate |

These are floors, not commitments: the joint plan (#78481) sets the real sequencing after
PR#490 PR-A, and each soak result sets the pace of the next phase.
