# CAI-1293 decision-audit mechanism fix — FULL audit (auditor #1: cc-quality)

**Auditor:** cc-quality (Opus 4.8). **Builder:** cc-fleet-health (propose-only). **Date:** 2026-08-23.
**Artifacts:** `reports/proposals/cai1293-mechanism-fix.proposal.sql` (+ wet-prove transcript + spec).
**Verdict: CHANGES** — 1 must-fix code bug + 1 residual for cai's call. Core design is SOUND and
independently verified (rolled-back live wet-prove; prod untouched, 0 leaked).

Fixes my 3 findings: CAI-991 (nonconforming verdict + mandatory tier), CAI-996 (≥2 lenses to close FULL),
CAI-1009 (unguarded/unrecorded FULL→NONE drop).

## MUST-FIX (BLOCKER) — the dodge-block RAISE errors with the wrong message
`enforce_audit_tier_change_guard` line 85: `RAISE EXCEPTION '...FULL->%% on % ...', NEW.audit_tier, NEW.decision_ref`.
`%%` is a LITERAL percent → the format string has **one** placeholder but **two** args → PostgreSQL raises
`too many parameters specified for RAISE` when the block path fires. **I reproduced this on the as-written
.sql** (first wet-prove run aborted with exactly that error).
- Fail-closed effect holds (any exception aborts the UPDATE, so the dodge is still blocked), BUT the operator
  gets a cryptic "too many parameters" instead of the CAI-1009 explanation + HINT — bad for diagnosing a
  legitimately-blocked re-tier.
- **More important: the wet-prove transcript's "✓ dodge → RAISE (refused)" did NOT faithfully exercise the
  as-written function** — the as-written function raises a *different* error than the transcript implies. A
  "refused" outcome was recorded without verifying it was the INTENDED refusal.
- **Fix:** `FULL->%%` → `FULL->%` (single placeholder), then **re-run the wet-prove against the corrected
  as-written .sql**. With that one-char fix I confirmed the block raises the correct CAI-1009 message and refuses
  (E1 below).

## RESIDUAL (cai's call, not a blocker) — the re-window multi-step escape
The dodge condition `OLD='FULL' AND NEW<>'FULL' AND NEW.challenge_status IN (challenge_window,unchallenged)`
is complete for the DIRECT single-UPDATE dodge. But a multi-step path still reaches the CAI-1009 harm — I
**empirically confirmed it** (rolled back):
1. FULL decision moved out of window (`challenge_status='accepted_by_audit'`), then `audit_tier` FULL→NONE →
   **ALLOWED** (out of window) — and correctly RECORDED (direction=drop).
2. `challenge_status` re-set to `challenge_window` (the tier trigger doesn't fire — `audit_tier` unchanged; no
   challenge_status lifecycle guard exists, only a requires-expiry check).
3. Result: `decision_audit_required()=FALSE`, and `enforce_challenge_window_timeouts` returns **`flipped`** —
   the 0-audit was-FULL decision would close `accepted_by_timeout`.

**Why it is a RESIDUAL, not a blocker:** it requires raw `challenge_status` UPDATEs. An actor who can raw-set
`challenge_status='accepted_by_audit'` has ALREADY closed the decision as accepted — the tier path grants no new
capability. It is a challenge_status-**lifecycle** gap (unguarded transitions), orthogonal to the tier axis
CAI-1009 named. And crucially, **this fix now RECORDS the drop** (decision_tier_changes), so unlike the original
CAI-1009 the escape leaves a detectable trail. **Options for cai:** (a) accept as a documented residual, leaning
on the tier-change log for detection; (b) add a challenge_status transition guard (no re-entry to challenge_window
from a closed state) — the cleaner structural fix, but its own change; (c) widen the tier-guard to block FULL→non-FULL
whenever the decision has 0 completed audits regardless of window. I lean (a)+(b-later): the log closes the
"unrecorded" half of CAI-1009 now; the transition guard is a separate hardening.

## Verified GOOD (independently or by reading)
- **E1 dodge block** (post typo-fix): FULL→NONE while `challenge_window` → RAISE CAI-1009, refused. ✓ (wet-proved)
- **Tier-change logging**: every change recorded with actor/old/new/reason/direction; drop/raise/set derived correctly. ✓ (wet-proved)
- **Challenge 2 (NULL-safety):** `IF NEW.audit_tier IS NULL THEN RETURN NEW` + column NOT NULL is airtight across OLD/NEW combos (LEGACY→NONE etc. are non-dodges since only FULL gates `decision_audit_required`). ✓ (read)
- **Challenge 3 (no cai override):** hard-block + always-record is consistent with the launch-clamp stance I endorsed. One nit: the HINT says "route a deliberate re-tier through a governed path" but no such path exists — reword to name the actual path (resolve/close the audit first), or provide a governed override. Non-blocking.
- **Challenge 4 (distinct-lens set):** `completed AND verdict=accepted AND lens IS NOT NULL, distinct` is correct — at close time all completed audits are already accepted (earlier arms block rejected/cnv/nonconforming/open), and the `decision_audits_one_per_auditor` UNIQUE (decision_ref, auditor_agent) makes distinct-lens⟹distinct-auditor, so ≥2 distinct lenses genuinely means ≥2 auditors (closes the "one auditor, two lenses" loophole). could_not_verify/open lenses correctly excluded. ✓ (read; relied on builder scratch transcript for execution)
- **Challenge 5 (view apply-time regen):** acceptable — the behavioural arms compute n_nonconforming + distinct-lens DIRECTLY, so the view is observability-only. CONDITION: the regenerated view must be re-verified at apply against the 3 specified additions (it surfaces the LEGACY-CANDIDATE / deferred retro-tier queue — a silent error there would hide governance state). Show the regen to an auditor at apply.
- **Challenge 6 (LEGACY backfill):** faithful to CAI-1296 — distinct queryable bucket, no false-NONE, deferred queue surfaced as LEGACY-CANDIDATE (not dropped). ✓ (read; builder counted 1517 LEGACY / 1022 LEGACY-CANDIDATE)
- **Parts B/C logic** (nonconforming verdict + unresolved + close-blocks + ≥2-lens): read as correct; relied on the builder's scratch transcript for execution (they need the view/close path; I did not independently re-run them — my independent wet-prove targeted the trigger/dodge surface where the adversarial risk lives).

## Bottom line
CHANGES: fix the `%%`→`%` RAISE and re-run the wet-prove on the corrected file (BLOCKER); adjudicate the
re-window residual (cai). Everything else conforms. Once the one-char fix lands and is re-proven, this closes my 3 findings.

---
## DELTA — rev2 (dfd33f4) — VERDICT: PASS (2026-08-23)
All of my CHANGES are addressed, verified at source:
- **MUST-FIX resolved, proven against the AS-SHIPPED .sql:** line 85 is now `FULL->% on %` (single placeholder,
  2 args). I extracted the guard fn **directly from dfd33f4's .sql** (no re-typing) and wet-proved the dodge block
  in a rolled-back txn — it raises the CORRECT message: `CAI-1009: refusing to drop audit_tier FULL->NONE on
  CQ-1293-DELTA while it is still closeable by timeout` (not "too many parameters"). Prod untouched, 0 leaked.
- **FIDELITY fix is REAL** (my key point): `cai1293_wetprove.py` now `extract_fn(name)` pulls each CREATE FUNCTION
  body straight from the shipping `.sql` (`_SQL`), failing LOUD (`SystemExit "FIDELITY FAIL"`) if a body isn't found —
  so a transcript can never again exercise a re-typed copy that diverged from the `.sql` (the exact hole that hid `%%`).
- **Ch3 HINT reworded** (line 88): the phantom "governed path" → "Resolve or close its audit first, then re-tier." ✓
- **Re-window RESIDUAL documented in the .sql** (lines 108–119) as cai's call, options (a)/(b)/(c), my lean (a)+(b-later) recorded — correctly NOT self-decided. ✓
- **Ch5 condition** (re-verify the view regen at apply — it surfaces LEGACY-CANDIDATE/839) noted in the apply-time spec. ✓

**PASS.** The code is applyable pending cai's grant + cai's residual (re-window) decision. Applying rev2 closes my
CAI-991/996/1009 findings; the re-window residual is a NEW, separate governance item for cai (not a regression, and
now detectable via the decision_tier_changes log).

---
## DELTA — rev3 (709e84c) — VERDICT: CHANGES (2 F1 grant fixes; F2 ok; core unchanged) (2026-08-23)
Rev3 folds cc-storefront's F1 (lock down decision_tier_changes) + F2 (LEGACY-CANDIDATE digest arm). Diff vs rev2
touches ONLY A.0b (table ACL), the guard's SECURITY DEFINER line, the B.3 view comment, and new Part D — core
a/b/c/RAISE/extract_fn UNCHANGED (confirmed). I role-by-role wet-proved F1 (rolled back, prod untouched):

**F1 GOOD:** anon SELECT DENIED; authenticated INSERT DENIED; guard trigger SECDEF w/ pinned `search_path=pg_catalog,public`
(correct — lets a non-service_role legit tier-UPDATE still write the log). RLS enabled.

**F1-a — CHANGES (MEDIUM-HIGH): the append-only guarantee is HOLLOW for service_role.**
Default privs on a bare new table grant `service_role` = DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE.
F1 does `REVOKE ALL FROM PUBLIC, anon, authenticated` — but NOT service_role — and `GRANT SELECT,INSERT TO service_role`
is purely ADDITIVE. Proven: after F1, `SET ROLE service_role; DELETE FROM decision_tier_changes` → **ALLOWED**; the
role still holds UPDATE/DELETE/TRUNCATE. So the A.0b comment ("APPEND-ONLY — not even service_role may UPDATE/DELETE")
is false — the substrate's own trusted role (what the app connects as) can rewrite/erase tier-change history, defeating
the tamper-evidence F1 exists to provide. NOTE: the sibling `decision_audits` is NOT the model here — it's deliberately
mutable (verdicts update); append-only needs an explicit revoke. **Fix:** `REVOKE UPDATE, DELETE, TRUNCATE ON
decision_tier_changes FROM service_role;` (leave only SELECT, INSERT), then re-wet-prove service_role DELETE/UPDATE DENIED.
The builder's transcript tested anon/authenticated denial + legit-logging but NOT service_role restriction — my
independent role test caught the gap.

**F1-b — CHANGES (LOW-MEDIUM): the console_readonly read is inert.**
`GRANT SELECT TO console_readonly` but console_readonly SELECT is **DENIED even with RLS OFF** (proven) — the grant
doesn't yield a working read, and there is no `console_ro` RLS POLICY (the sibling `decision_audits` has an explicit
`decision_audits_console_ro FOR SELECT TO console_readonly USING(true)`; F1 has none). So either the console read path
is intended (→ replicate decision_audits' full console_readonly access — grant + policy + whatever schema access it
needs — and WET-PROVE console_readonly actually reads; adding a policy alone won't fix the RLS-off denial) OR it isn't
(→ drop the inert grant, service_role-sink only, per my new-table checklist). Don't ship a grant that doesn't work.

**F2 — OK (apply-time, logically sound):** the LEGACY-CANDIDATE view arm (placed parallel to UNTIERED-CANDIDATE, in-window
condition, before WINDOW-OPEN — the placement bug the wet-prove caught is correctly fixed) + Part D digest count
`IN ('UNTIERED-CANDIDATE','LEGACY-CANDIDATE')` correctly prevents the NULL→LEGACY backfill from zeroing the digest's
untiered-candidate watch (the false all-clear that would hide my 839 never-tiered gap; builder proved old→0/new→~25 —
the ~25 is the in-window subset of the ~1022 LEGACY-CANDIDATE, consistent). CONDITION (now LOAD-BEARING, elevated from Ch5):
the view arm + digest edit are apply-time regens — Nazim must byte-verify BOTH at apply and an auditor should sight the
regenerated view/digest, since a silent error there re-hides the retro-tier queue.

**Verdict: CHANGES** — fix F1-a (real append-only via REVOKE from service_role) + F1-b (wire or drop the console grant),
re-wet-prove the role matrix. Core + F2 conform. Once F1 is corrected, rev3 is applyable (pending cai grant + re-window residual call).

---
## DELTA — rev4 (23e1970) — VERDICT: PASS (2026-08-23)
Rev4 folds F3 (my F1-a) + F4 (my F1-b), both auditors converged. Diff vs rev3 = **A.0b + harness ONLY**; core
a/b/c/RAISE/extract_fn + F2 **byte-unchanged** (git-confirmed, grep=0). Independently re-proven with a PROD-FIDELITY
role matrix (table CREATEd on the REAL `public` schema so it inherits the real default ACL — the exact thing a scratch
schema is blind to; rolled back, prod verified untouched):
- **F3 (F1-a) FIXED:** A.0b now `REVOKE ALL … FROM PUBLIC, anon, authenticated, service_role`. Proven: `SET ROLE
  service_role` → UPDATE **DENIED**, DELETE **DENIED** (append-only real), while INSERT/SELECT still work. service_role
  has `rolbypassrls=true`, so the GRANT (not RLS) is the append-only gate — correct.
- **F4 (F1-b) FIXED:** two policies mirror the sibling `decision_audits` — `decision_tier_changes_console_ro FOR SELECT
  TO console_readonly USING(true)` + `_service_only FOR ALL TO service_role`. Proven: console_readonly SELECT returns
  rows; **mutation** (drop the console_ro policy) → console_readonly reads **0** = the policy is genuinely load-bearing
  (RLS-on denies without it). The service_only policy is inert under bypassrls but kept for parity — fine.
- anon SELECT / authenticated INSERT: DENIED.

**Evidence correction (my own, in the interest of accuracy):** my rev3 F1-b claim "denied even RLS-off = grant itself
ineffective" was a TEST ARTIFACT — my rev3 harness could not `SET ROLE console_readonly` (my session's membership seam),
and the exception surfaced as a mislabelled "PERMISSION DENIED" I wrongly attributed to the SELECT. The finding's
SUBSTANCE (RLS-on + grant + no policy → console_readonly reads nothing, so a policy is required) was correct and is now
mutation-proven; but the specific rev3 diagnostic was flawed. F4 is the right fix regardless.

**Verdict: PASS.** All my findings are closed. The full CAI-1293 fix now conforms across every part (a/b/c core, RAISE,
extract_fn fidelity, F1 lockdown F3+F4, F2 digest). Applyable pending cai's grant + cai's re-window-residual decision.
The F2 view+digest apply-time byte-verify condition still stands (load-bearing). Applying rev4 closes my CAI-991/996/1009.
