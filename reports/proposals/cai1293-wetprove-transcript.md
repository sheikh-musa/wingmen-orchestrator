# CAI-1293 mechanism-fix — WET-PROVE transcript (rev 2)

**Rev 2 (2026-08-23):** applies cc-quality #32207 CHANGES — (1) MUST-FIX `%%`→`%` in the guard RAISE
(the as-written `.sql` would have raised "too many parameters" instead of the CAI-1009 message on the
block path); (2) HINT reworded (dropped the non-existent "governed path"); (3) re-window RESIDUAL
documented in the `.sql` for cai. **Fidelity fix (cc-quality's key point):** the harness now
EXTRACTS the guard/close/unresolved function bodies DIRECTLY from `cai1293-mechanism-fix.proposal.sql`
(`extract_fn`), so the transcript can never again exercise a re-typed copy that diverges from what
ships — the divergence that hid the RAISE bug in rev 1.

**Method:** isolated scratch schema `cai1293_wp`, **REAL data** copied from live (Nazim #32172
fidelity condition), single **ROLLED-BACK** txn. **Safety:** `public` read with ACCESS SHARE only;
every fix DDL hits `cai1293_wp` tables — **zero ACCESS EXCLUSIVE on live `strategic_decisions`**;
the close-fn's `agent_messages` insert + all else roll back. **Post-run: `cai1293_wp` exists? 0.**
Harness: `reports/proposals/cai1293_wetprove.py` (re-runnable).

## FIDELITY baseline (real, non-test)
`NULL=1517  FULL=135  NONE=10  total=1662`

## PART A — mandatory tier + drop-guard + record  (CAI-988/F1 + CAI-1009)
```
backfilled NULL->LEGACY: 1517 rows;  post-backfill NULL count = 0
LEGACY bucket queryable = 1517;  LEGACY-CANDIDATE (cai's deferred retro-tier queue) = 1022
CHECK{FULL,NONE,LEGACY} + NOT NULL applied
✓ (a) CAI-1009 dodge FULL->NONE in-window on REAL CAI-RESP-1111
      -> "CAI-1009: refusing to drop audit_tier FULL->NONE on CAI-RESP-1111 while it is still closeable by timeout"
      (rev 2: the CORRECT message now fires — rev 1's %% would have raised "too many parameters")
✓ (a) junk tier ('JUNK')  -> refused    ✓ (a) NULL tier -> refused by the column NOT NULL (clean)
✓ (a) legit LEGACY->FULL  -> RECORDED: actor=wetprove-cc-fleet-health LEGACY->FULL reason set direction=raise
```
(As in rev 1: on a FULL-in-window row the junk UPDATE is caught by the DODGE guard before the domain
CHECK; the CHECK domain {FULL,NONE,LEGACY} is independently proven by the 1517-row LEGACY backfill.)

## PART B — 'nonconforming' verdict + coherence  (CAI-991)
```
✓ (b) verdict='nonconforming' -> ACCEPTED by the widened CHECK;  verdict='garbage' -> refused
✓ (b) decision_audit_unresolved('nonconforming',done,unresolved)=True;  (…,resolved)=False
```

## PART C — close_decision_by_audit  (CAI-991 block + CAI-996 >=2 lenses)
```
✓ (c) FULL, 1 accepted lens  -> REFUSED: "FULL tier requires >=2 distinct completed accepted lenses (have 1) — CAI-996"
✓ (c) FULL, 2 DISTINCT accepted lenses -> 'closed'
✓ (b/c) a 'nonconforming' verdict present -> REFUSED: "an auditor found it NONCONFORMING"
```

## RESIDUAL (cc-quality #32207; cai's call — documented in the .sql, NOT fixed here)
Multi-step re-window escape (move FULL out of window → drop tier → re-open window → 0-audit closes by
timeout). Needs raw `challenge_status` UPDATEs and NO new capability; a challenge_status-LIFECYCLE gap
orthogonal to the tier axis; the drop now leaves a trail in `decision_tier_changes`. Options for cai:
(a) accept + lean on the log [shipped default]; (b) a challenge_status transition guard [separate];
(c) block FULL→non-FULL at 0 completed audits regardless of window. cc-quality leans (a)+(b-later).

## Apply-time (not in scratch)
`decision_audit_state` view re-declaration (add `n_nonconforming` + LEGACY/LEGACY-CANDIDATE arms) is
an APPLY-TIME regen from live `pg_get_viewdef` (against drift). Close computes n_nonconforming +
distinct-lens DIRECTLY, so behaviour is fully covered; the view change is observability only.
