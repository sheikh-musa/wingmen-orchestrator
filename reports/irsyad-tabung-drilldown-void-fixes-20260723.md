# irsyad tabung — 2 client fixes (drill-down + report-void) — op#6461/6463

**From:** cc-orchestrator (hub) · client-facing (irsyad/goumlyne), money-adjacent (tabung). Repo: ihsanos (`~/wingmen/projects/ihsanos-deployed` on Mini; Studio ihsanos-wt/* for lanes). Data: goumlyne silo `73339164…`, active batch 6 "client roster". **Do the fix in an ISOLATED worktree off origin/main.**

## Issue #2 — CONFIRMED BUG (safe, display-only): unreturned drill-down shows RETURNED names
**Client:** for P6 FEZ, header shows 6 unreturned; tapping "unreturned" lists the *returned* students instead of the 6 unreturned.
**Verified data (goumlyne batch 6, P6 FEZ):** banked 8 + closed 3 + returned 2 = **13 returned** ("13 (68%)"); **issued 6** = unreturned; total 19. Counts are correct.
**Root cause:** `src/app/dashboard/tabung/keluarga/class-completion-report.tsx:64` — the class drill-down calls `getKkStudentsByClassAction(classId)`, which returns status IN (returned,counted,banked,closed) = the RETURNED students. On the unreturned view, expanding a class therefore shows returned names.
**Fix:** the unreturned drill-down must fetch the UNRETURNED (status='issued') students — reuse/extend the existing "tins still out" path (`src/actions/tabung-keluarga.ts:~1128-1160`, `.eq("status","issued")`). If the report shows BOTH returned and unreturned per class, wire each expander to its matching list and label clearly. Display-only, no data mutation — SAFE. Proof: expand P6 FEZ unreturned → the 6 issued students (by name/tin), NOT the 13 returned.

## Issue #1 — INVESTIGATE FIRST, gate the money-adjacent part: Elly void an unsubmitted/unsigned report
**Client:** "how can Elly delete/void/cancel a report which is not submitted — she has not signed it."
**Context:** tabung banking uses maker-checker + a signed reconciliation report (`closed_via_report_id` on tins; `tabung-keluarga.ts:602` maker-checker). A report that's created-but-not-submitted/signed is a draft.
**Task:** trace the report lifecycle (create → submit → sign → close-tins). Determine: is there a void/discard for a DRAFT (unsubmitted, unsigned) report? If YES → tell the client the exact steps. If NO → it's a feature gap; propose a discard-draft action **scoped to unsubmitted/unsigned only** (must NOT allow voiding a signed/closed report that already banked tins — that's money integrity). **Voiding anything that has closed/banked tins is GATED — route to cai + hub, do NOT self-implement.** A pure draft-discard (no tins closed yet) is likely safe but confirm the state model first.

## Report back
Post `agent_messages` → cc-orchestrator (update) with: #2 the commit + the real drill-down proof (P6 FEZ unreturned = 6 issued names); #1 your finding (steps that exist, or the proposed gated design). Hub verifies + updates the client. Client already told: #2 being fixed, #1 being confirmed.
