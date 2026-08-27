# cc-quality FULL audit — 2nd-PASS, dispatch #29797/#29806/#29832

Repo: `sheikh-musa/ihsanos`. Auditor: cc-quality (2nd PASS; cc-storefront ran 1st PASS per #29832). Identity check done first (RAILS): live OAuth token hashes to cc-quality's designated key (`syed-oauth-token`) — confirmed at source; unrelated pane env-var leak (`ORCH_AGENT_ID=orch-console`) flagged separately to cc-fleet-health per orch-console #29836, does not affect this audit's attribution (bus writes done under `SET app.current_agent_id='cc-quality'`).

## PR #394 — feat/cai1200-preparer-history-5 — **FAIL**

Dispatch #29832 claimed "#394 stacks on that CORRECTED base (mig206+mig085-re-commit)." **Verified false at source:** `git merge-base --is-ancestor b1f674e 02b9c0f` (hist5 HEAD) → NO. #394 carries its own earlier, divergent copy of mig206 (commit b2e8c77, same day, pre-fix):
- Missing the `BEGIN;...COMMIT;` transactional wrapper CAI-RESP-180 required (confirmed present in the corrected b1f674e copy, absent in b2e8c77 — diffed directly).
- Missing migration 085 (`sch_staff_read_rls.sql`) entirely — the very grant #394's own preparer-path minors-exclusion depends on. It exists live on both silos but was never committed to `main`/this branch (confirmed via `git ls-tree`).

Independent of the base issue, the preparer-path minors-exclusion in `getPersonGivingHistoryAction` (src/actions/reports.ts) is **structurally fail-open**, exactly the risk #29797 asked me to check:
```ts
const { data: studentRow } = await supabase.from("sch_students").select("id")...maybeSingle();
if (studentRow) { return { data: [], error: null }; }
```
Only `data` is destructured — `error` is discarded. If the RLS grant (mig085) is ever tightened/revoked, or the query errors for any reason, `studentRow` comes back null/undefined and the code falls through to serve the minor's real donation/tin history. The code comment calls the page-level mig192 persons carve-out "the data-layer belt on top" implying two independent layers — verified mig192 IS fail-closed by row-carve-out (confirmed in 192_persons_student_carveout.sql: preparer/cashier/viewer carved out of student `persons` rows entirely), but this action-level check is NOT independently fail-closed — it's a single point of failure with the appearance of defense-in-depth.

**Recommend:** rebase #394 onto b1f674e (get the wrapper + mig085 for real), and change the sch_students existence-check to fail-closed (treat any query `error` as "deny", not "not a student").

## PR #395 — feat/cai1216-topdonors-preparer-4 @f62c737 (rebuild) — **PASS**

Confirmed f62c737 genuinely is on the corrected base (`merge-base --is-ancestor b1f674e f62c737` → YES; mig206 has BEGIN/COMMIT; mig085 present).
- TDZ fix verified: `availableReports` now declared after the `canSeeTopDonors` hook (diffed).
- View-log fix verified: `writeAuditLog`'s returned `.error` is now inspected and captured, not just thrown exceptions.
- (c) `person_id IS NOT NULL` on both arms of `donor_leaderboard_all`: confirmed by reading mig206 directly.
- (d) No code path derives a join/ranking key from `parsePooledSource`: confirmed — it's referenced only in `donations-list.tsx` (display) and its own test file; `reports.ts` never imports it.
- Minors-exclusion structural NOT-EXISTS against `sch_students`: confirmed present on both arms.

**New disclosed latent gap, verified accurate:** the audit-note comment claims a soft-deleted student is invisible to preparer's NOT-EXISTS (asymmetric `deleted_at` filtering) but not to org_admin's. Confirmed by reading the actual RLS policies: mig085's "Staff can see students" policy filters `deleted_at IS NULL`; mig015's `org_admin full access on sch_students` policy does not. So a soft-deleted student's donations would be excluded from org_admin's ranking but could reappear in a preparer's ranking. Currently disclosed, non-blocking (1 soft-deleted row / 0 donations per the comment — not independently re-verified against live data by me). Recommend tracking as a follow-up ticket rather than leaving only as a code comment.

**Recommendation on view-log fail-open-vs-closed (per #29786 ask):** keep best-effort/fail-open for initial go-live now that `.error` is actually inspected and alerted — blocking the report entirely on a logging infra hiccup is a worse regression than a monitored gap. Escalate to cai only if CAI-1216 treats the view-log as a hard compliance requirement rather than accountability-trail best-effort.

## PR #396 — feat/fajar-manual-send — **PASS**

- `FAJAR_MANUAL_SEND_LIVE = false as boolean` confirmed; `sendDonationCommsAction` short-circuits to `"gated"` before ever calling `sendReceiptEmailAction` (verified code order).
- `hasEmail` never decrypts: server-side resolver in `page.tsx` selects only `id`, filters `.not("email_encrypted","is",null)` — the encrypted column itself is never selected or sent to the client.
- No silent no-op: every disabled/blocked path (`no_receipt`, `no_email`, `gated`) surfaces a toast message; click always round-trips.
- Test suite exercises the real shipped gate constant and the real action (not a stand-in) — ran live: 5/5 pass, including an assertion pinned to the shipped `FAJAR_MANUAL_SEND_LIVE === false` value.

## PR #397 — fix/cai1218-pooled-source-donor-cell — **PASS**

- Variant-A-first ordering in `parsePooledSource` confirmed: the ambiguous-liaison check runs before the source-marker parse and always returns the fixed `POOLED_SOURCE_UNCONFIRMED` placeholder — never derived text, never a raw name.
- No aggregation impact: this PR only touches `donations-list.tsx` (display); `reports.ts`/`donor_leaderboard_all` never reference `parsePooledSource` or `notes` (confirmed in #395's review, same repo).
- Ran the existing `pooled-source.test.ts` unit suite live: 8/8 pass.
- Gap: no render-level test for the new `DonorName`/`pooledSourceFor` wiring itself — only the underlying pure function is tested. Low severity (display-only, simple wiring, reviewed by inspection) but flagging per the test-coverage-gate charter item. Did not independently re-verify the "~18 Reception rows render correctly" claim against live goumlyne data (no browser/prod access from this session) — verified by code-logic inspection only, stating that honestly rather than asserting a live check I didn't perform.

## Summary
| PR | Verdict |
|---|---|
| #394 | **FAIL** — not on corrected base (verified false claim), fail-open minors-exclusion |
| #395 @f62c737 | PASS |
| #396 | PASS |
| #397 | PASS |

---

## PR #398 — feat/merge-redesign-cai1219 — **PASS** (one test-coverage gap flagged, non-blocking)

Repo: `ihsanos-merge` worktree, HEAD `89cd553`. Assigned via #29842; audited against the now-ratified CAI-RESP-1222 (#29869) since it landed before this audit completed.

- **merge_persons.ts (execute path) genuinely untouched:** `git diff origin/main...HEAD -- "*merge-persons*"` = empty. Its own pre-existing test file (`merge-persons.test.ts`) still runs unmodified and passes (ran live, included in the 39/39 below).
- **(a) TIER-2 family-suppression, verified in `merge-candidates-core.ts`:** `if (group.length !== 2) continue;` — any hash group of size ≠2 is suppressed entirely (0 pairs emitted, not just capped); exactly-2 groups additionally gated by `hasFullRecordBeyond` on both records. NRIC tier-1 has no such check (`if (group.length >= 2) emitAllPairs(...)`, no suppression) — matches spec. Tier-3 name+address is a true conjunction: the group key is `null` (skipped) unless BOTH normalised name and address are non-empty. All three verified by reading the pure function directly, plus its dedicated unit suite passes.
- **(b) Preparer-read exclusion as its own surface:** confirmed in both `detectMergeCandidatesAction` (pre-filters students before pairing, `effectiveForPreparer` forced true for any non-admin regardless of the caller's argument) and `listMergeCandidatesAction` (re-applies the identical `fetchStudentIds` filter independently over the persisted-marker rows, not inherited from detection). RLS on `person_merge_candidates` also carries the same correlated-NOT-IN exclusion as the primary layer (mig207), app-layer as the belt — verified by reading the migration's policy SQL directly.
- **mig207 SECDEF helper (`merge_org_student_person_ids`) authz:** confirmed `REVOKE ALL FROM PUBLIC` + `REVOKE EXECUTE FROM anon` + `GRANT EXECUTE TO authenticated` (in that order), authz guard `p_org_id IN (SELECT auth_user_org_ids())`, `deleted_at IS NULL` filter on the source table, `SET search_path`. All present and correctly ordered.
- **Fail-closed under CAI-RESP-1222, verified live:** `fetchStudentIds` throws when the RPC errors ("refusing to serve without minors-exclusion"); both call sites are in try/catch blocks that convert the throw to `INTERNAL_ERROR` (data: null) — no candidate list, admin or preparer, is ever served when the exclusion set can't be determined. `detectMergeCandidatesAction` has a dedicated test forcing the RPC to a realistic `42501` permission error and asserting `INTERNAL_ERROR` — ran live, passes.
- **Nav/route gating:** `/dashboard/people/merge` stays org_admin-only in both `route-access.ts` and `supabase-middleware.ts` CORE_ACCESS plus page-level `requireRole`. New `/dashboard/people/duplicates` is org_admin+preparer in both maps + page-level `requireRole`. Confirmed `duplicates-client.tsx` only calls `detectMergeCandidatesAction(true)` / `listMergeCandidatesAction(true)` / `flagMergeCandidateAction` — never `resolveMergeCandidateAction` or anything from `merge-persons.ts` — so there is genuinely no execute path reachable from the preparer surface.
- Ran the full new test surface live: `merge-candidates.test.ts` + `merge-candidates-core.test.ts` + `merge-persons.test.ts` = 39/39 pass.

**Finding (non-blocking, flagged under the now-binding CAI-RESP-1222):** `listMergeCandidatesAction` calls the same `fetchStudentIds` minors-exclusion check as `detectMergeCandidatesAction`, but only `detectMergeCandidatesAction`'s describe block has a test that forces the RPC to error and asserts fail-closed. `listMergeCandidatesAction`'s block only covers the happy-path drop/keep cases. The code path is shared and I've verified it by direct inspection (same throw → same catch → same INTERNAL_ERROR shape), so I'm not withholding PASS over it, but per CAI-1222 item 2 this is exactly the class of gap the new criterion exists to catch on the list-action's own audited surface — recommend adding the equivalent forced-error test before/alongside merge.

**Disclosed, non-blocking, verified accurate (same class as the #395 finding):** `merge_org_student_person_ids` filters `deleted_at IS NULL`, so a soft-deleted student escapes this exclusion too — same asymmetry as donor_leaderboard_all vs the org_admin sch_students policy. Author disclosed it in-migration; recommend the same follow-up ticket cover both.

**Disclosed, non-blocking:** the UPDATE RLS policy on `person_merge_candidates` grants preparer a full-row update (not status-only) — author left an explicit TODO. Blast radius is limited since `merge_persons` (the actual execute path) is unchanged and still requires org_admin hand-review/confirm downstream, so a preparer rewriting a queue row's fields can at most pollute the triage queue, not force a merge. Agree with author's non-blocking classification; recommend tightening in the same follow-up pass as the two items above rather than as a separate ticket.
