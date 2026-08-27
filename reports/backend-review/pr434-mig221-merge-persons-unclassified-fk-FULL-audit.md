# PR #434 / mig221 — merge_persons classify 2 unclassified FKs (CAI-1273) — cc-quality FULL money/PII audit

**VERDICT: PASS** (data-integrity + PII SECDEF, CAI-1170). Propose-only; console wet-proves both silos → cai grant (CAI-1264) → apply. I did NOT apply/wet-prove. One flagged reversibility gap = **safe fast-follow** (ruling below), one LOW observation. Neither blocks.
**Auditor:** cc-quality (opus-4-8, `.quality_model` carve-out) · **Date:** 2026-08-22 · **Head:** db0883fd · **Dispatch:** cc-irsyad #31380 (P2, client-blocking).

## ADDITIVE-ONLY — byte-identical confirmed
Extracted the merge_persons body from mig178 (the sole current definition on main) vs mig221 and diffed: the ONLY changes are the 4 claimed additions —
1. `v_has_candidates BOOLEAN := false;` decl, 2. `'donation_appreciation_letters.person_id'` appended to REPARENT_KEYS, 3. the `ELSIF v_key IN ('person_merge_candidates.person_a_id','person_merge_candidates.person_b_id') THEN v_has_candidates := true;` branch, 4. the resolve SPECIAL block.
**Byte-identical (untouched):** actor-bind guard (`auth.uid()=p_actor_id`), org_admin check, identity hard-blocks (differing verified-NRIC / user_id conflict), FREEZE_KEYS, the generic collision-safe REPARENT loop, the person_roles + donor_consent SPECIAL branches, the FAIL-CLOSED drift-guard RAISE, field-reconcile, loser soft-delete + snapshot, and the inline hash-chained audit.
**Grants preserved:** mig221 contains NO GRANT/REVOKE statement → `CREATE OR REPLACE` carries mig178's grants unchanged (REVOKE anon+service_role, GRANT authenticated only). No privilege change.

## SPECIAL branch correctness (person_merge_candidates resolve) — sound
`UPDATE person_merge_candidates SET deleted_at=now(), status='merged', updated_at=now() WHERE org_id=p_org_id AND (person_a_id=p_loser_id OR person_b_id=p_loser_id) AND deleted_at IS NULL` + a `person_merge_reparent op='resolve'` bookkeeping row per resolved candidate.
- **Cannot violate the 3 constraints** — it never touches person_a_id/person_b_id, so `CHECK(a<>b)`, `CHECK(a<b)`, and `UNIQUE(org_id,a,b)` are structurally unreachable. (Resolve, not reparent — exactly right for a queue whose pair becomes moot when the loser disappears.)
- `status='merged'` is a valid value — `person_merge_candidates.status CHECK (status IN ('proposed','merged','not_merged'))` (mig207:92-93).
- Org-scoped (`org_id=p_org_id`); only live rows (`deleted_at IS NULL`).
- **Does NOT touch person_merge_snapshot** (the immutable merge audit) — it's a different table, in FREEZE_KEYS, not enumerated for reparent. Frozen as before.

## Drift-guard, audit, op-CHECK, residency, collision — all PASS
- **Fail-closed drift-guard intact:** the new candidates ELSIF sits before the final `ELSE RAISE 'merge_unclassified_fk'`; any OTHER unmapped persons-FK still RAISEs. The new branch catches only the 2 named columns.
- **Hash-chain / audit-log:** unchanged (byte-identical).
- **op-CHECK widening additive:** mig178 had `op IN ('reparent','role_softdelete','skip_unique','consent_propagate_revoke')`; mig221 = same 4 + `'resolve'` (DROP+ADD, no rows rewritten).
- **Per-silo (empirically verified RO):** donation_appreciation_letters EXISTS on goumlyne, **ABSENT on ceayj** → REPARENT entry live on goumlyne, harmless no-op on ceayj (per-silo pg_constraint enumeration). person_merge_candidates on BOTH silos, **0 rows (0 live) on both** → the SPECIAL branch is a no-op today.
- **Number 221 collision-free** (main highest 220; only #434 claims it). Self-committing BEGIN/COMMIT (console strips per CAI-756). lint:all EXIT 0 (schema-drift/rls-invariant/money-float/minors-exclusion all clean, 170 migs).

## REVERSIBILITY GAP — ruling: SAFE FAST-FOLLOW (not must-fix-in-mig)
reverse_merge (mig178) has no `op='resolve'` case, so a reversed merge will NOT restore a resolved person_merge_candidates row to live/proposed. Ruling rationale:
- **Empirically inert today** — 0 candidate rows on BOTH silos, so `op='resolve'` never fires → the missing reverse case is unreachable.
- mig221 is **client-blocking** (merges RAISE on the unclassified FKs now); it correctly stays within CAI-1273's exact 2-FK scope. The builder rightly FLAGGED the gap rather than silently widening scope to reverse_merge — correct discipline.
- **HARD gate on the fast-follow:** before the preparer-triage queue goes live with pending person_merge_candidates rows, reverse_merge must gain an `op='resolve'` case. ⚠ The follow-up ALSO needs mig221's resolve bookkeeping to capture the **pre-state** (old_status + old deleted_at) — today it records only `op='resolve'` + old_person_id, which is insufficient to restore the prior status on reverse. Because it's 0 rows today, both changes can land together in the fast-follow with zero data migration. Coord/console: track tied to triage go-live.

## LOW observation (non-blocking)
The SPECIAL branch keys on `deleted_at IS NULL`, not `status='proposed'` — so a live `status='not_merged'` candidate (a preparer's "do not merge" decision, kept but not soft-deleted) touching the loser would be flipped to `'merged'` + soft-deleted. Resolving it is correct (the loser is gone → the proposal is moot), but it overwrites the `not_merged` decision label. Minor; if preserving that dismissal matters, scope to `status='proposed'` or record old_status in the resolve op (which the reversibility fast-follow will want anyway). Not a blocker (0 rows; resolving a superseded proposal is the right behavior).

**Bottom line: PASS. Additive-only, byte-identical to mig178 except the 4 CAI-1273 additions; SPECIAL branch is constraint-safe and snapshot-safe; fail-closed drift-guard and grants intact; residency no-ops verified. Reverse_merge op='resolve' is a gated safe fast-follow (must land before triage go-live, with pre-state capture). Console wet-proves both silos → cai grant CAI-1264 → apply.**

---
## DELTA RE-AUDIT — CAI-1277 fold (round-trip symmetry) — PASS (2026-08-22, @63189ae5)
cai folded the reverse_merge fix INTO mig221 (not my fast-follow). Re-audited the delta db0883fd → 63189ae5. All 5 console points PASS:
- **(a)** merge SPECIAL branch WHERE now `... AND status = 'proposed' AND deleted_at IS NULL` (lines 315-316) — resolves ONLY proposed+live rows. = my prior LOW, now the load-bearing invariant.
- **(b)** reverse_merge gains `ELSIF r.op = 'resolve'` → `UPDATE person_merge_candidates SET deleted_at=NULL, status='proposed', updated_at=now() WHERE id::text=r.row_pk AND org_id=r.org_id`.
- **(c) round-trip byte-identical** on all FUNCTIONAL columns (status, deleted_at, person_a_id/b_id, match_tier, org_id). Only `updated_at` advances (T0→T2) — correct: it is a mutation timestamp that SHOULD change; restoring T0 would wrongly hide the round-trip. proposed→(merge)→merged+softdel→(reverse)→proposed.
- **(d)** non-'proposed' rows (merged/not_merged) UNTOUCHED both ways: merge skips them (WHERE status='proposed'); reverse only restores rows that carry an op='resolve' record (⟺ were 'proposed'), so a decided row is never resurrected. Because person_merge_reparent stores no old_status, reverse restores unconditionally to 'proposed' — lossless ONLY because merge resolves 'proposed' exclusively (the (a) tightening is what makes it sound).
- **(e) additive-only:** merge core byte-identical to the prior PASS except the one `AND status='proposed'` line; reverse_merge byte-identical to mig178 (62L→70L) except the 8-line op='resolve' case — every other replay case (reparent/role_softdelete/consent_propagate_revoke/skip_unique-noop) unchanged. All 5 op values handled; unknown ops impossible (CHECK-bounded).
- **Grants preserved:** mig221 has ZERO GRANT/REVOKE ON FUNCTION → CREATE OR REPLACE carries mig178's authenticated-only grants for BOTH merge_persons and reverse_merge (REVOKE anon+service_role, GRANT authenticated). No privilege change.
- **Constraint-safe restore:** `uq_person_merge_candidates_pair` is a FULL UNIQUE (org_id,person_a_id,person_b_id) (no partial WHERE) — a soft-deleted row still holds the slot, so no duplicate pair can form while resolved → reverse's un-delete cannot UNIQUE-collide.
- **Per-silo (RO, current):** 0 candidate rows on both silos (still inert; fold is correct-by-construction regardless). reverse_merge exists both silos (mig178 CORE). lint:all EXIT 0 (schema-drift/money-float/rls-invariant/minors-exclusion clean, 170 migs).

**DELTA VERDICT: PASS.** merge↔reverse is round-trip lossless for the candidate queue; the 2-FK core + all other merge/reverse logic byte-identical. Supersedes the pre-fold #31389 PASS. Propose-only — console wet-proves BOTH silos (recommend an empirical round-trip: seed a proposed candidate on the loser → merge → assert resolved → reverse → assert restored to proposed byte-identical; + a not_merged row untouched both ways) → cai grant CAI-1264 → apply.
