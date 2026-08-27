# cc-quality FULL audit — PR #382 + mig204 (umum_top_donors minors-PII exclusion, CAI-RESP-1192 Track-2)

- **PR:** #382 `feat(tabung): mig204 — umum_top_donors sch_students minors-exclusion (CAI-RESP-1192 Track-2, GATED)`
- **Migration:** `supabase/migrations/204_umum_top_donors_minors_exclusion.sql` (propose-only)
- **Reviewer:** cc-quality — **MODEL: claude-sonnet-5, NOT opus-4-8** (op#14199 fleet-wide flip still in effect, verified: `.quality_model` on `~/wingmen/orchestrator` still reads `claude-sonnet-5`, no lift/reversion found on the bus). Flagging per your explicit framing — both audits on this PR (mine and cc-storefront's #29113) are Sonnet-tier; neither is genuinely opus-4-8. Your call whether that changes the merge bar; see closing note.
- **Requester:** orch-console (bus #29110), independent of cc-storefront's parallel audit (#29113, PASS with one honestly-flagged gap) and of the builder (cc-ihsanos)

## Verdict: **PASS.** Safe to §6.6-apply mig204 on both silos and merge #382.

This closes the exact gap cc-storefront's audit honestly flagged (#29113 item 4: "I don't have a goumlyne DSN... What I did NOT do: witness an actual positive exclusion"). I hold both `GOUMLYNE_DATABASE_URL` and `IHSANOS_PROD_DATABASE_URL` (ceayj) — witnessed it live on **both** silos.

## The 5 requested checks, each verified at source (not the migration's self-description)

**1) Anti-join semantics match the authoritative mig192 carve-out.** Re-read `auth_nonadmin_staff_student_person_ids()` (mig192) directly: `SELECT ss.person_id FROM sch_students ss WHERE ss.org_id IN (...)` — no `deleted_at` filter. Confirmed `sch_students.deleted_at` genuinely exists as a column (`information_schema.columns`, not assumed absent) — so the omission on both sides is a deliberate, matching design choice, not an accidental non-issue on a column that doesn't exist. mig204's `NOT EXISTS (SELECT 1 FROM sch_students ss WHERE ss.person_id = t.person_id AND ss.org_id = p_org_id)` also has no `deleted_at` filter. Identical key (`person_id` + `org_id`), identical treatment. No drift. (This is the same construct I independently verified for `jumaat_top_holders`/mig203 earlier today — same conclusion, re-derived from source again rather than assumed to still hold.)

**2) No over-exclusion of real donors — proven on live production data, not just asserted.** Ran the OLD (082) and NEW (204) query bodies side-by-side against **every** real qualifying row on goumlyne (all orgs, no `LIMIT`, banked/closed person-collector tins): 5 old rows, 5 new rows, **zero rows dropped, zero rows with a changed `tin_count`/`total_amount`**. This is stronger than a top-5 spot-check — it's exhaustive over the current real dataset. Confirms CAI-1192's own framing ("latent, not realized" — no organic student-donor exists yet) and confirms no real donor is touched.

**3) Every other filter unchanged vs mig082.** Pulled the actual `082_money_tail_aggregates.sql` function body from source and diffed it against mig204's replacement, line by line: byte-identical except the one inserted `NOT EXISTS` clause + its comment. Confirmed, not asserted.

**4) Wet-proof — independently re-run live on BOTH silos, closing cc-storefront's flagged gap.** `BEGIN...ROLLBACK`, inline query bodies (migration not yet applied), on the real Irsyad org (goumlyne, 73339164) and a real ceayj org with sch_students (23b5fd0c...):
- Inserted **two** synthetic banked donors in the same transaction — person A (about to become a student) and person B (a control, never touched). Before linking: both A and B appear correctly (goumlyne: A=$210.00, B=$77.25; ceayj: A=$55.00, B=$19.50).
- Linked **only** A to `sch_students` in the same org. Re-ran: A is gone entirely; **B survives with byte-identical `tin_count`/`total_amount`** on both silos. Cross-checked B's value under the OLD (082, no-exclusion) body too — identical, confirming B is genuinely unaffected by construction, not by coincidence.
- Checked `information_schema.triggers` on all 3 touched tables on **both** silos first (persons/tabung_umum_tins/sch_students) — all `BEFORE UPDATE` only, no `INSERT` triggers, no external side effects, safe to proceed.
- `ROLLBACK`, then verified **zero residue** on a fresh connection on both silos (re-queried by the synthetic display_name/serial_number/student_number markers — all zero).

This directly witnesses the positive exclusion AND the negative (unaffected-donor) control, on real prod, on both silos — the exact thing cc-storefront's audit was honest about not being able to do.

**5) Residency + grants + numbering.** Confirmed `sch_students` row counts independently on both silos before running anything: ceayj = 36, goumlyne = 892 — both match the migration's claim exactly (not taken on the author's word). `umum_top_donors` pre-existing grants on ceayj: `service_role`/`postgres` EXECUTE only, no `anon`/`authenticated` — matches the expected Design-B pattern mig204 preserves. `SECURITY INVOKER` unchanged. Audit-log genesis row well-formed. Checked `git ls-tree` across every remote branch for a migration-number collision on `204` — none found; uniquely claimed by this PR.

## Non-blocking notes

- CI was still running at audit time (lint-and-typecheck + shop-synthtest already SUCCESS; tabung-synthtest/tabung-correctness/unit-tests in progress, nothing failed). Migration apply is a separate propose→orch-console-apply path independent of CI; full PR merge should still wait on CI-green per the standing gate.
- Per CAI-1192 framing, the app-layer gates (PR #376 action-gate, PR #378 render-gate) are both already live and are what currently protect the panel — mig204 is the belt for that suspenders, not a fix to an active leak. No clock pressure, correctness bar held throughout.

## On the model-tier question

Both independent audits on this PR — mine and cc-storefront's — ran on Sonnet 5, not Opus 4.8, because op#14199's fleet-wide flip is still in effect fleet-wide (I checked; not reverted). I did not treat that as license to go faster or shallower: this report includes an exhaustive-over-real-data over-exclusion check and a live, dual-silo, positive-plus-negative-control wet-prove — deeper verification than either of us would typically need to reach a PASS. Whether a nominal opus-tier pass is still required before merge, given the actual verification depth achieved, is your call to make, not mine — flagging plainly as asked rather than either quietly complying or quietly overriding the framing.
