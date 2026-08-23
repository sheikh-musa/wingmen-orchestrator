# CAI-1293 mechanism-fix — WET-PROVE transcript (rev 3)

**Rev 3 (2026-08-23):** folds cc-storefront #32230 (cai-accepted CAI-RESP-1298) — F1 (lock down the
`decision_tier_changes` log + SECURITY DEFINER trigger) + F2 (keep `audit_board_digest`'s
untiered-candidate watch alive across the LEGACY backfill). Both empirically exercised on scratch
per Nazim #32237. (rev2 = the RAISE `%%`→`%` fix + harness fidelity; rev1 = the original.)

**Method:** isolated scratch schema `cai1293_wp`, **REAL data**, single **ROLLED-BACK** txn; the
guard/close/unresolved fn bodies are EXTRACTED from the shipping `.sql` (`extract_fn`, fail-loud on
mismatch). **Safety:** `public` read with ACCESS SHARE only; all DDL hits `cai1293_wp` — **zero
ACCESS EXCLUSIVE on live `strategic_decisions`**; everything rolls back. **Post-run: schema
exists? 0.** Harness: `reports/proposals/cai1293_wetprove.py`.

## FIDELITY baseline (real, non-test): `NULL=1518  FULL=135  NONE=10  total=1663`

## PART A — mandatory tier + drop-guard + record (CAI-988/F1 + CAI-1009)
```
backfill NULL->LEGACY: 1518 rows; post NULL=0; LEGACY=1518; LEGACY-CANDIDATE=1022
✓ dodge FULL->NONE in-window on REAL CAI-RESP-1111 -> "CAI-1009: refusing to drop ... while it is still closeable by timeout"
✓ junk tier -> refused    ✓ NULL tier -> refused by column NOT NULL    ✓ legit LEGACY->FULL -> RECORDED (actor/old/new/reason/dir=raise)
```

## F1 — decision_tier_changes lock-down + SECDEF trigger (cc-storefront #32230)
Simulated the real public default privs (anon=SELECT, authenticated=INSERT/SELECT/UPDATE/DELETE,
verified via `pg_default_acl`) on the scratch log, then applied the A.0b lock-down (RLS + REVOKE +
service_role SELECT,INSERT append-only + console_readonly SELECT), then exercised BY ROLE:
```
✓ F1 anon          SELECT/INSERT/UPDATE/DELETE : all DENIED
✓ F1 authenticated SELECT/INSERT/UPDATE/DELETE : all DENIED
✓ F1 SECDEF: SET ROLE authenticated (REVOKEd from the log) did a legit LEGACY->NONE tier change
             -> the SECURITY DEFINER trigger STILL wrote the record (as owner), actor=authtest.
             (A SECURITY INVOKER trigger would have failed here — proving SECDEF is load-bearing.)
```
=> the "never unrecorded again" trail is now append-only + not anon-readable/authenticated-erasable,
AND the lock-down does not break legit non-service_role tier updates.

## PART B — 'nonconforming' verdict + coherence (CAI-991)
```
✓ nonconforming ACCEPTED by CHECK; garbage verdict refused
✓ decision_audit_unresolved('nonconforming', done, unresolved)=True; (…, resolved)=False
```

## PART C — close_decision_by_audit (CAI-991 block + CAI-996 >=2 lenses)
```
✓ FULL, 1 accepted lens  -> REFUSED "FULL tier requires >=2 distinct completed accepted lenses (have 1) — CAI-996"
✓ FULL, 2 DISTINCT accepted lenses -> 'closed'
✓ a 'nonconforming' verdict present -> REFUSED "an auditor found it NONCONFORMING"
```
(The new arms compute their inputs — distinct-lens, n_nonconforming — DIRECTLY from decision_audits,
so they're proven on scratch data regardless of the view chain.)

## F2 — audit_board_digest untiered-candidate watch survives the backfill (cc-storefront #32230)
Proven DIRECTLY on the scratch (post-backfill) table with the digest's exact candidate predicate:
```
pre-backfill untiered-candidate watch (in-window) = 25
post-backfill OLD digest predicate (audit_tier IS NULL only)     = 0   <- the regression (false all-clear)
post-backfill NEW digest predicate (IS NULL OR 'LEGACY' via arm) = 25  <- fix restores all 25 candidates
```
=> the NULL->LEGACY backfill zeroes the OLD watch; the LEGACY-CANDIDATE view arm + the digest FILTER
change (`audit_state IN ('UNTIERED-CANDIDATE','LEGACY-CANDIDATE')`) restore it.

## Bugs the wet-prove caught (fixed) + proving-artifact notes
- The LEGACY-CANDIDATE view arm must sit PARALLEL to UNTIERED-CANDIDATE (BEFORE the WINDOW-OPEN arm),
  else an in-window LEGACY candidate is swallowed by WINDOW-OPEN and never reaches the digest. Fixed
  in the B.3 spec + proven.
- The guard's SECDEF `SET search_path` pin means the scratch prove must prepend the scratch schema
  (documented adaptation; only the pin VALUE differs, the logic is byte-identical via extract_fn).
- A scratch VIEW over scratch tables mis-binds its base-table refs to `public` (a proving artifact,
  NOT a fix defect) — so F2 and the digest are proven DIRECTLY on scratch data, and the view re-decl
  + digest FILTER edit are APPLY-TIME artifacts Nazim byte-verifies (per #32237) + the auditors
  independently re-prove.
