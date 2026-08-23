# CAI-1293 mechanism-fix — WET-PROVE transcript

**Run:** 2026-08-23, cc-fleet-health. **Method:** isolated scratch schema `cai1293_wp`,
**REAL data** copied from live (Nazim #32172 fidelity condition), single **ROLLED-BACK** txn.
**Safety:** `public` read with ACCESS SHARE only (the `CREATE TABLE … AS SELECT` copy); every
fix DDL (`ADD CONSTRAINT` / `SET NOT NULL` / trigger) hits `cai1293_wp` tables — **zero ACCESS
EXCLUSIVE on live `strategic_decisions`**; the close-fn's `agent_messages` insert + all else roll
back. **Post-run: `cai1293_wp` schema exists? 0** — nothing persisted. Harness:
`scratchpad/cai1293_wetprove.py`.

## FIDELITY baseline (real, non-test)
`NULL=1517  FULL=135  NONE=10  total=1662` — matches the cai1272 sweep distribution (NULLs have
grown from 1514 as decisions accumulate; the point stands).

## PART A — mandatory tier + drop-guard + record  (CAI-988/F1 + CAI-1009)
```
backfilled NULL->LEGACY: 1517 rows
post-backfill NULL audit_tier count = 0        (NOT NULL now satisfiable)
LEGACY bucket queryable, count = 1517          (distinct, not a silent NONE — CAI-RESP-1296)
LEGACY-CANDIDATE (cai's deferred retro-tier queue), count = 1022   (queryable: audit_tier='LEGACY' AND tier_candidate)
CHECK{FULL,NONE,LEGACY} + NOT NULL applied
✓ (a) CAI-1009 dodge FULL->NONE in-window on REAL CAI-RESP-1111 -> RAISE (refused)
✓ (a) junk tier ('JUNK')                        -> refused
✓ (a) NULL tier                                 -> refused by the column NOT NULL (clean; null-guard added)
✓ (a) legit LEGACY->FULL                        -> RECORDED in decision_tier_changes
       actor=wetprove-cc-fleet-health  LEGACY->FULL  reason='wetprove: legit re-tier'  direction=raise
```
Note for auditors: on CAI-RESP-1111 (a FULL-in-window row) the junk-tier UPDATE is caught by the
DODGE guard (FULL->non-FULL-in-window) *before* the domain CHECK — both reject it; the CHECK domain
{FULL,NONE,LEGACY} is independently proven by the 1517-row LEGACY backfill succeeding under it and
the guard's own record path. (A dedicated CHECK-only junk test on a non-FULL row would make this
unambiguous — cheap to add if you want it.)

## PART B — 'nonconforming' verdict + coherence  (CAI-991 / 987-F4)
```
✓ (b) verdict='nonconforming'                   -> ACCEPTED by the widened CHECK
✓ (b) verdict='garbage'                         -> refused by the CHECK
✓ (b) decision_audit_unresolved('nonconforming', completed, unresolved) = True   (a negative, like rejected/cnv)
      decision_audit_unresolved('nonconforming', completed, resolved)   = False  (resolves like the others)
```

## PART C — close_decision_by_audit  (CAI-991 block + CAI-996 >=2 lenses)
```
✓ (c) FULL decision, 1 accepted lens            -> REFUSED: "FULL needs >=2 distinct completed accepted lenses (have 1) CAI-996"
✓ (c) FULL decision, 2 DISTINCT accepted lenses -> 'closed'
✓ (b/c) a 'nonconforming' verdict present        -> REFUSED: "NONCONFORMING (CAI-991)"
```
(Non-FULL tiers keep the single-accepted-lens close — the >=2-lens arm is gated on `audit_tier='FULL'`.)

## Not exercised in scratch (apply-time / observability — flagged for the audit)
- **decision_audit_state view re-declaration** (add `n_nonconforming` + the LEGACY/LEGACY-CANDIDATE
  audit_state arms): specified as an APPLY-TIME regen from live `pg_get_viewdef` (against drift). The
  close-fn does NOT depend on the view's new columns (it computes `n_nonconforming` + distinct-lens
  DIRECTLY — defence in depth, proven above), so behaviour is fully covered; the view change is
  observability only. Auditors: confirm the regen spec in the .sql is faithful at apply time.
