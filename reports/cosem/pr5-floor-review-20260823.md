# COSEM PR#5 floor-review — `phase1/omr-trial-deltas` (cosem-platform)

**Reviewer:** orch-console (Nazim), opus-4-8. **Date:** 2026-08-23. **Head:** `de299c62`.
**Verdict: NEEDS-CHANGES** (propose-only merge gated on F3; grading/import correctness gated before the real trial).

## Scope (verified at source)
6 files, +450/-1. Only `src/actions/exams-paper.ts` alters existing code (+ its matched migration); the other four source/test files are net-new. `jszip` already on main (not a new dep).

## Load-bearing safety claim — VERIFIED TRUE
"Additive / `gradeTheory` + `effectiveGrade` untouched": `src/modules/exams/grading.ts` and `scoring.ts` byte-identical to main; new `gradeTheoryPerUnit`/`canAttempt` have **zero callers in `src/`** (tests only); `effectiveGrade`'s sole prod caller (`src/actions/exams.ts:371`) unchanged. Grading/import deltas (a)/(c) are genuinely inert until wired. Tests exercise the shipped functions with real inputs (no vacuous mocks).

## Findings (my disposition)

**F3 — MERGE-GATE (migrate-before-deploy).** `exams-paper.ts` `generatePaperBatch` (wired live at `paper-console.tsx:36`) now **unconditionally** writes `trainee_name`/`service_no` on every `paper_answer_sheets.insert(sheetRows)` — including the no-roster path. Those columns exist only in the propose-only migration `20260824090000`. cosem-platform-demo auto-deploys on merge → deploying before the migration is applied to the demo DB (`ywrpttpxwfcoodovxhsr`) breaks ALL paper generation (PGRST204). **Gate:** apply the additive migration to the demo DB FIRST, verify columns exist, THEN merge/deploy. Doctrine: migrate-before-deploy, all silos.

**F2 — BEFORE-WIRING (fail-open).** `unit-grading.ts:56-62`: `passed = units.length>0 && units.every(u=>u.passed)` only checks units *present in the snapshot*. No expected-unit registry exists. A unit that fails to load/import is never evaluated and cannot block a pass → a candidate "passes per-unit" while an entire unit went ungraded. Contradicts the locked rule (every NFPA unit independently ≥60%). **Fix:** grade against an explicit expected-unit set; fail-closed if any expected unit is absent. Must land before grading is relied on for the real exam.

**F1 — BEFORE-DRY-RUN (silent-drop, uncounted).** `examview.ts:70-88`: importer keeps only the single largest `<w:tbl>`; rows in any other table are never seen and **not counted in `skippedRows`**. Word routinely splits a long table across sections into separate `<w:tbl>`. For a no-silent-drop bar, this defeats the count guarantee. Validated 279/280 on the real single-table bank, so latent for THIS bank — but see F6.

**F4 — BEFORE-DRY-RUN (silent-drop).** `examview.ts:120-130` `splitStemOptions`: once options started, a non-`OPTION_LINE` line is discarded → an option spanning two Word paragraphs loses its trailing paragraph silently, truncating answer text. If it's the correct option, the sheet shows a wrong/partial answer with no error. No test covers multi-paragraph options.

**F6 — BEFORE-DRY-RUN (auditability).** Importer returns only `skippedRows: number`, not identity/reason. The 279/**280** parsed = 1 skipped — is it the header, or a lost question? Cannot tell. **Need:** surface each dropped row (reason code) and confirm the 1/280 was the header, not content, before trusting the bank load for the dry run.

**F5 — BEFORE-REAL-BANK (silent-misparse).** Fixed-index cell destructure (`parseExamViewRow:104`) breaks on horizontally-merged cells (`w:gridSpan` collapses a `<w:tc>`, shifting columns → wrong ANSWER letter). Lazy `<w:tbl>` regex can't handle nested tables (first inner `</w:tbl>` truncates). Harden if any future export uses merged/nested cells.

**F7-F9 — NITS/LATENT.** F7 cap hardwired to 60 decoupled from `perUnitThreshold` (inconsistent only if threshold ever tuned >60). F8 pass computed on rounded percent (unreachable for small per-unit counts). F9 `canAttempt` management-approval is a bare boolean with no provenance — must become an attributable authorization before the ceiling is enforced for the real exam.

**F10 — RESIDENCY (8-Sep gate, already tracked).** Delta (d) writes real trainee name+serviceNo (UAE gov PII) to `paper_answer_sheets`. 26-Aug dry run is synthetic (safe). Real-roster write-target MUST be a provisioned UAE ADCDA silo, not the shared demo DB (TENANT-RESIDENCY-001; LAYER-VOCAB-001 — migration/PR name no store ref). Console drives provisioning.

## Merge sequence (routed to cc-cosem-exams)
1. Apply migration `20260824090000` to demo DB `ywrpttpxwfcoodovxhsr` → verify `trainee_name`/`service_no` present (unblocks F3).
2. Fix F1 + F4 + F6 (importer: count/surface every dropped row; multi-table + multi-paragraph; confirm the 1/280) — before the dry-run bank load is trusted.
3. Fix F2 (grade fail-closed on missing expected unit) — before grading is wired for the real exam.
4. F5 hardening + F7-F9 nits — note; F5 before a merged-cell export.
5. Re-request merge → console re-verifies + merges. F10 residency = separate 8-Sep gate (console drives).
