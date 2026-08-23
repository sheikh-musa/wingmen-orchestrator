# CAI-1293 mechanism-fix — WET-PROVE transcript (rev 4)

**Rev 4 (2026-08-23):** folds the CONVERGED rev3 findings from BOTH opus auditors — cc-quality #32253
(F1-a/F1-b) + cc-storefront #32263 (F3/F4), cai-accepted, Nazim #32267:
- **F3 (append-only was still hollow via `service_role`):** a bare public table inherits `pg_default_acl`
  = `service_role` `arwdDxtm` (full DML) + `rolbypassrls`. rev3 REVOKEd public/anon/authenticated but
  NOT service_role, so the additive `GRANT SELECT,INSERT` left the inherited UPDATE/DELETE/TRUNCATE —
  the app's own role could still ERASE the trail. Fix = **deny-by-default incl service_role**
  (`REVOKE ALL … FROM …, service_role` then `GRANT SELECT,INSERT`).
- **F4 (console read was inert):** RLS-on + 0 policy ⇒ `console_readonly` (`rolbypassrls=false`) reads 0
  rows — the GRANT is dead. Fix = mirror the sibling `decision_audits` policy pair (`console_ro` SELECT
  + `service_only` ALL). service_role's policy is inert (bypassrls); the GRANT is the append-only gate.

**Why rev4 exists / method change (decisive):** the scratch schema `cai1293_wp` has **NO default ACL**,
so a scratch `GRANT SELECT,INSERT` yields ONLY those privs and **append-only FALSELY appears to hold** —
the scratch prove was STRUCTURALLY BLIND to F3 (it slipped rev1-3). So rev4 adds a **PROD-FIDELITY arm**
that creates the log table on the **REAL `public` schema** (inheriting the real `pg_default_acl`),
applies the shipped A.0b, and asserts the full role matrix by `SET ROLE` — then rolls it back. This is
storefront's root-cause and Nazim's mandate. (rev3 = F1+F2 fold; rev2 = the RAISE `%%`→`%` fix + harness
fidelity; rev1 = the original.)

**Method:** single **ROLLED-BACK** txn; guard/close/unresolved fn bodies AND the CREATE+A.0b DDL are
EXTRACTED from the shipping `.sql` (`extract_fn`/`extract_block`, fail-loud on mismatch). Two ACL arms
run the SAME extracted A.0b: (i) scratch `cai1293_wp` (illustrative), (ii) **PROD-FIDELITY** on live
`public` (authoritative). **Safety:** `public` read ACCESS SHARE only for the data copy; the strategic_
decisions DDL hits `cai1293_wp` — **zero ACCESS EXCLUSIVE on live `strategic_decisions`**; the PROD-
FIDELITY arm's `CREATE TABLE public.decision_tier_changes` locks ONLY the new table and is rolled back
in a savepoint; a transient `GRANT console_readonly TO postgres` (needed to impersonate the reader)
also rolls back. **DEAD-MAN'S-SWITCH: a fresh post-run connection asserts `cai1293_wp` gone AND
`public.decision_tier_changes` gone (fail-loud if not); verified live post-run — CLEAN, nothing
persisted; `SET ROLE console_readonly` fails again (set_option restored).** Harness:
`reports/proposals/cai1293_wetprove.py`.

## FIDELITY baseline (real, non-test): `NULL=1519  FULL=135  NONE=10  total=1664`

## PART A — mandatory tier + drop-guard + record (CAI-988/F1 + CAI-1009)
```
backfill NULL->LEGACY: 1519 rows; post NULL=0; LEGACY=1519; LEGACY-CANDIDATE=1022
✓ dodge FULL->NONE in-window on REAL CAI-RESP-1111 -> "CAI-1009: refusing to drop ... while it is still closeable by timeout"
✓ junk tier -> refused    ✓ NULL tier -> refused by column NOT NULL    ✓ legit LEGACY->FULL -> RECORDED (actor/old/new/reason/dir=raise)
```

## F1 (scratch) — lock-down + SECDEF trigger (cc-storefront #32230)
Scratch simulates the inherited default privs (anon=SELECT, authenticated & service_role = full DML),
applies the A.0b lock-down, then BY ROLE — ⚠️ illustrative only; the AUTHORITATIVE ACL proof is below:
```
✓ F1 anon          SELECT/INSERT/UPDATE/DELETE : all DENIED
✓ F1 authenticated SELECT/INSERT/UPDATE/DELETE : all DENIED
✓ F1 SECDEF: SET ROLE authenticated (REVOKEd from the log) did a legit LEGACY->NONE tier change
             -> the SECURITY DEFINER trigger STILL wrote the record (as owner), actor=authtest.
             (A SECURITY INVOKER trigger would have failed here — proving SECDEF is load-bearing.)
```

## F3/F4 — PROD-FIDELITY role matrix on live `public` (AUTHORITATIVE), rolled back
Creates the log table on the REAL `public` schema so it inherits the substrate `pg_default_acl`, applies
the shipped A.0b (extracted), seeds one owner row, and asserts EACH role by `SET ROLE` — **15/15**:
```
inherited default-privs BEFORE A.0b:
  anon          = REFERENCES,SELECT,TRIGGER
  authenticated = DELETE,INSERT,REFERENCES,SELECT,TRIGGER,UPDATE
  service_role  = DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE   <- the F3 hole scratch is blind to
grants AFTER A.0b:  console_readonly=SELECT ;  service_role=INSERT,SELECT ;  anon/authenticated = GONE
✓ service_role     INSERT allowed        ✓ service_role SELECT allowed (rows=1)
✓ service_role     UPDATE  DENIED (F3)   ✓ service_role DELETE DENIED (F3)   ✓ service_role TRUNCATE DENIED (F3)
✓ anon             SELECT/INSERT/UPDATE/DELETE : all DENIED
✓ authenticated    SELECT/INSERT/UPDATE/DELETE : all DENIED
✓ console_readonly SELECT returns rows (rows=1) — F4: the policy makes the GRANT LIVE
✓ console_readonly INSERT DENIED
```
=> append-only now holds for **every** role incl. `service_role` (enforced by the GRANT, since
service_role bypasses RLS), and `console_readonly` can actually READ the trail (F4). The trail is no
longer anon-readable, authenticated-erasable, OR service_role-erasable, and the lock-down does not break
legit non-service_role tier updates (SECDEF, above).

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
pre-backfill untiered-candidate watch (in-window) = 23
post-backfill OLD digest predicate (audit_tier IS NULL only)     = 0   <- the regression (false all-clear)
post-backfill NEW digest predicate (IS NULL OR 'LEGACY' via arm) = 23  <- fix restores all 23 candidates
```
=> the NULL->LEGACY backfill zeroes the OLD watch; the LEGACY-CANDIDATE view arm + the digest FILTER
change (`audit_state IN ('UNTIERED-CANDIDATE','LEGACY-CANDIDATE')`) restore it.

## Bugs the wet-prove caught (fixed) + proving-artifact notes
- **F3 (the reason for rev4):** append-only was hollow via `service_role` — the scratch schema has NO
  default ACL so it was STRUCTURALLY BLIND to the inherited `service_role=arwdDxtm` (both opus auditors
  caught it on a public BEGIN..ROLLBACK check; rev4 adds the PROD-FIDELITY arm so the harness itself
  catches it). Lesson: an ACL/deny-by-default claim MUST be proven against a target that inherits the
  real `pg_default_acl`, never a bare scratch schema. (CAI-1018/1019 deny-by-default.)
- The LEGACY-CANDIDATE view arm must sit PARALLEL to UNTIERED-CANDIDATE (BEFORE the WINDOW-OPEN arm),
  else an in-window LEGACY candidate is swallowed by WINDOW-OPEN and never reaches the digest. Fixed
  in the B.3 spec + proven.
- The guard's SECDEF `SET search_path` pin means the scratch prove must prepend the scratch schema
  (documented adaptation; only the pin VALUE differs, the logic is byte-identical via extract_fn).
- A scratch VIEW over scratch tables mis-binds its base-table refs to `public` (a proving artifact,
  NOT a fix defect) — so F2 and the digest are proven DIRECTLY on scratch data, and the view re-decl
  + digest FILTER edit are APPLY-TIME artifacts Nazim byte-verifies (per #32237) + the auditors
  independently re-prove.
