# Fix: class-completion drill-down hides UNRETURNED students (client bug)

**From:** cc-orchestrator (hub) · **Source:** irsyad partner UAT — "P6 Elvira: unreturned=9 but I see only 4 students" · **Date:** 2026-07-17
**Repo:** ihsanos · **Worktree:** `~/wingmen/ihsanos-wt/kk-drilldown-fix` (branch `fix/kk-class-completion-drilldown`, off origin/main 14f5684). AUTHORED build — no merge/deploy; hub reviews + eyeballs + deploys.

## The bug (already diagnosed — verified against live data + code)
`getKkStudentsByClassAction` in `src/actions/tabung-keluarga.ts` (~line 1608) is the class-completion drill-down (expand a class → list its students). Its Step-2 tin query filters:
```
.in("status", ["counted", "banked", "closed"])
```
This **excludes `issued` (unreturned) tins**. So the drill-down only lists students whose tins were already returned/counted, and **hides students who still have a tin out (unreturned)** — the exact students the report exists to surface. Confirmed on live data (goumlyne, org Madrasah Irsyad Zuhri, P6 ELVIRA class `24b8952f-c8d6-46cb-810c-7be4821b1342`): 9 tins in `issued` status across 9 active students (all with person records), all correctly counted in the class row's "unreturned: 9" — but none appear in the drill-down. The count is right; the student list is wrong.

## The fix
Make the drill-down show **every student in the class with their tin status**, so unreturned students appear alongside returned ones, with a **clear returned / not-returned marker per student** (that's what the client asked for, and it's the point of a class-completion report — to chase who hasn't returned).

Concretely:
- Include `issued` (and any other in-flight statuses like `returned` if not already) in the drill-down aggregation — do NOT restrict to counted/banked/closed. Scope to the ACTIVE batch (match the class-completion parent view's batch scoping, so counts reconcile with the class row).
- For each student return: student_number, display_name, and a per-status breakdown — e.g. `unreturned_count` (issued) + `returned_count` (returned/counted/banked/closed) + total_amount (amount only meaningful once counted). A student with only an issued tin must appear with unreturned=1, returned=0.
- **Reconcile to the class row:** the sum of per-student unreturned in the drill-down MUST equal the class row's "unreturned" number (9 for P6 Elvira). Add a test asserting this reconciliation on a fixture with a mix of issued + returned + multi-tin students.
- Update the drill-down UI (`src/app/dashboard/tabung/keluarga/class-completion-report.tsx` drill-down render) to show the returned/not-returned marker per student. Keep it clean + mobile-friendly (client-facing, ihsan bar).

## Watch-outs
- Multi-tin students: a student may have >1 tin in different statuses — aggregate correctly (don't double-count students; show their per-status counts).
- Don't change the class-row rollup logic (the "unreturned/returned/total/amount" tallies are correct) — only the drill-down student list is broken.
- Batch scoping: the class row counts are per the active batch — the drill-down must use the SAME batch scope so they reconcile.

## Proof / report back to cc-orchestrator
- Unit + a reconciliation test (per-student unreturned sums to the class-row unreturned) on a fixture reproducing the P6-Elvira shape (issued + returned + multi-tin).
- tsc 0, lint 0, next build green. REAL pasted output.
- Screenshots of the fixed drill-down (desktop + mobile) showing unreturned students with the marker — client-facing, eyeball quality.
- Branch + SHA. AUTHORED only — hub re-runs tests + eyeballs + deploys. This is live client UAT feedback, so move fast.
