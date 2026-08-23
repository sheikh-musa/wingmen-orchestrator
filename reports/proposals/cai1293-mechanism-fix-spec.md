# CAI-1293 — decision-audit mechanism fix (PROPOSE-ONLY build spec)

**Builder:** cc-fleet-health (cai-confirmed, CAI-RESP-1294). **Dispatched:** Nazim #32119/#32132 (P1).
**Boundary (NON-NEGOTIABLE):** PROPOSE-ONLY. This is mechanism DDL applied DIRECTLY to the live
substrate (NOT a repo `migrations/*.sql`). I draft a reviewable `.sql` + a SCRATCH-db wet-prove
transcript and **never apply to the live substrate, never write `strategic_decisions`/grant rows**
(charter §3b `assert_ops_only`). Flow: draft → **cc-quality (auditor #1) + cc-storefront (auditor #2,
on opus post-recycle) FULL audit** → **cai grant** → **Nazim wet-proves + applies**.

**Source of truth:** cc-quality report `reports/backend-review/cai1272-mechanism-cluster-audit.md`
(the 3 nonconforming findings) + sweep #32124 (no surviving evidence the drop fired historically —
because NO tier-history is retained, which is itself the gap (a) closes). All schema facts below
read from the LIVE substrate 2026-08-23.

## The one combined by-construction fix (3 parts)

### (a) `audit_tier` mandatory + a drop-guard — CAI-988/F1 + CAI-1009
**Why:** CAI-1009 EXERCISED (rolled-back) that an `UPDATE strategic_decisions SET audit_tier=FULL→NONE`
succeeds UNGUARDED and is recorded NOWHERE, flipping `decision_audit_required()` FALSE so a 0-audit
decision closes `accepted_by_timeout`. The builder-drops-own-tier-to-escape-audit dodge.

**Live schema:**
- `audit_tier text, is_nullable=YES, default=NULL`.
- CHECK `strategic_decisions_audit_tier_check`: `(audit_tier IS NULL) OR (audit_tier = ANY('FULL','NONE'))`.
- Distribution (non-test, 1659): **NULL 1514, FULL 135, NONE 10.**
- `decision_tier_escalations` = the only tier-touch log (tracks RAISES; drops unrecorded).
- 5 existing triggers on strategic_decisions: cai_decision_announce_insert/update,
  cai_decision_autoclose_announce, trg_challenge_window_requires_expiry, trg_strategic_decisions_provenance.

**DDL design:**
1. **Backfill the 1514 NULLs — ⚠️ GOVERNANCE FORK for cai (I propose, cai decides):**
   NOT NULL cannot be added while 1514 NULL rows exist. Options:
   - **(A) NULL → 'NONE'** — simplest; matches how they were treated (closed-by-timeout, unaudited).
     Risk: rubber-stamps 839 `tier_candidate=TRUE` should-have-been-FULL rows as NONE (hides the
     never-tiered gap the sweep sized).
   - **(B) NULL → a new `'LEGACY'` (or `'UNTIERED'`) tier value** (extend the CHECK) — preserves the
     honest "was never tiered" fact; the NOT-NULL + guard then applies going FORWARD without
     back-asserting a NONE judgment on 1514 historical rows. **RECOMMEND — most honest, no false NONE.**
   - **(C) tier_candidate heuristic backfill** (candidate→FULL else NONE) — most work, re-opens 839
     as FULL-owed retroactively (a board flood); likely out of scope for the mechanism fix.
   → Draft with (B) as default; present all three to cai in the proposal, let cai pick.
2. `ALTER TABLE strategic_decisions ALTER COLUMN audit_tier SET NOT NULL;` (after backfill).
3. Replace the CHECK to drop the `IS NULL OR` branch (+ add 'LEGACY' if (B)).
4. **Guard trigger** `trg_audit_tier_drop_guard` BEFORE UPDATE OF audit_tier:
   - Nazim's spec: "BLOCKS a silent drop (or at minimum logs actor+reason so a drop is never
     unrecorded again)." → Do BOTH tiers, RECOMMEND block-the-escape + always-record:
     - If OLD.audit_tier='FULL' AND NEW.audit_tier<>'FULL' AND the decision has ANY audit rows OR is
       still in challenge_window → **RAISE** (the dodge; matches close_decision's block-not-record shape).
     - Otherwise (a legitimate re-tier) → RECORD actor+old+new+reason into `decision_tier_escalations`
       (or a new `decision_tier_changes` log) so NO tier change is ever unrecorded again.
   - Actor: `current_setting('app.current_agent_id', true)` (same provenance seam the identity trigger uses).
   - ⚠️ must coexist with the 5 existing triggers — verify fire order doesn't race the announce/autoclose ones.

### (b) add `'nonconforming'` verdict — CAI-991 / 987-F4
**Live:** `decision_audits_verdict_check`: `(verdict IS NULL) OR (verdict = ANY('accepted','rejected','could_not_verify'))`.
Also `decision_audits_completion_coherent`: `(verdict IS NULL) = (completed_at IS NULL)`.
**DDL:** drop+recreate the verdict CHECK adding `'nonconforming'`. Trivial + additive. Note downstream:
`decision_audit_state` counts n_accepted/n_rejected/n_could_not_verify — a `nonconforming` verdict is
NOT accepted, so `close_decision_by_audit` (n_accepted≥1, and the block-arms) already treats it as
non-closing by construction — but ADD an explicit `n_nonconforming>0 → block` arm in (c) for clarity,
mirroring the could_not_verify arm (don't let a new terminal verdict silently round to non-blocking).

### (c) FULL ⇒ ≥2 DISTINCT completed lenses before close — CAI-996
**Live:** `close_decision_by_audit(p_decision_ref, p_closed_by)` gates n_accepted≥1 / n_open=0 /
n_rejected=0 / n_could_not_verify=0, then sets challenge_status='accepted_by_audit'. NO lens check.
`decision_audit_state` already exposes `audit_tier` + `lenses` (aggregate) + n_* — use them.
**DDL:** in close_decision_by_audit, after the existing arms, add:
```
IF v.audit_tier = 'FULL' AND cardinality of DISTINCT completed non-null lenses < 2 THEN
    RAISE EXCEPTION 'cannot close %: FULL tier needs >=2 distinct completed lenses (CAI-996)';
END IF;
```
(compute distinct-lens count either from v.lenses if it is already DISTINCT-non-null, else a scalar
subquery over decision_audits WHERE decision_ref AND completed_at IS NOT NULL AND lens IS NOT NULL AND
verdict='accepted'). VERIFY v.lenses semantics before relying on it. Keep it a MANUAL-fn guard (996's
mitigation: no trigger auto-invokes close, so this is the right, low-blast-radius place).

## Wet-prove plan (SCRATCH db, NOT live)
Spin a throwaway PG (or a rolled-back txn on live per CAI-1009's own method — BEGIN…ROLLBACK, is_test
rows only, NEVER commit), and prove each arm:
- (a) FULL→NONE drop on a decision with audits/in-window → RAISES; a legit re-tier → RECORDS a row.
  NOT NULL rejects a NULL insert. Backfill leaves 0 NULL.
- (b) inserting verdict='nonconforming' now ALLOWED; a garbage verdict still rejected.
- (c) a FULL decision with 1 accepted lens → close RAISES; with 2 distinct accepted lenses → 'closed'.
  A NONE/normal decision path unchanged (regression).
Capture the transcript. Roll back clean (0 leaked rows), like the cai1272 exercise.

## Routing
Proposal (`.sql` + wet-prove transcript + this spec) → cc-quality + cc-storefront FULL audit
(money-path rigor) → cai grant → Nazim wet-proves + applies to substrate. I do NOT apply.

## STATUS
Fully grounded (schema + fns + view + fork identified) 2026-08-23. NEXT: draft the `.sql` + wet-prove
(scratch/rolled-back) + route. Drafting deferred to a focused pass so sensitive governance DDL isn't
authored at the tail of a marathon session — grounding captured here so the draft is fast + safe.
