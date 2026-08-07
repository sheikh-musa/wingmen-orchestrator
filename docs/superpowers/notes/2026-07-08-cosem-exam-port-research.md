# COSEM Exam & Assessment — Port Research (current adcda → new platform)

Reference for the exam-port implementation plan. Source: code audit of `~/wingmen/projects/cosem-adcda` (2026-07-08). All writes today go through Firebase Callable Functions; Firestore rules deny direct client writes. Three distinct subsystems.

## 1. Theory (auto-graded MCQ)
- Question bank: hardcoded JS `src/data/scdfTheoryQuestionBank.js` — 30 frozen questions, each `{id, text (EN/AR bilingual), options[3], correctAnswer(letter)}`. **No difficulty/topic/media/per-option fields** (some questions reference images that don't exist in data). Answer key duplicated in `functions/index.js:5620-5651` + `config/scdf_settings`.
- Flow (`src/pages/ScdfTheory.jsx`): trainee auto-resolves `traineeId` (from `linkedTraineeId`); seeded Fisher-Yates question shuffle (`${traineeId}:${sessionSeed}`, options NOT shuffled); all questions on one page, no timer/pagination; submit requires all answered → `submitScdfTheoryAttempt`.
- Scoring: **server-side** (`functions/index.js:5718-5805`). `scorePercent = round(correct/total*100)`; pass threshold `settings.passingScore` default **70**.
- Stored: `scdfTheoryAttempts` (append-only) + `scdfStatus/{traineeId}.theoryLatest`.
- Routing: trainees auto-redirect to `/scdf/theory` as home (`App.jsx:167`).

## 2. Practical (binary checklist)
- Rubrics: hardcoded `src/data/scdfPracticalRubrics.js` — 4 types (`five_basic_knots`, `scba_five_safety_checks`, `extension_ladder`, `s1w3`), each `{criteria:[{id,label,maxPoints}]}`, every criterion `maxPoints:1` (binary).
- Assessor-only (`requiredPermission="scdf"` = admin/trainer/super_admin). Enters 3-digit military ID → matched vs roster; scores each criterion 0/1.
- Scoring server-side (`:5844-5909`): criteria coerced binary, `percentage = round(total/max*100)`, **no pass/fail threshold** (percentage only).
- Stored: `scdfPracticalAssessments` (append-only) + `scdfStatus/{traineeId}.practicalLatest`.

## 3. Skill Sheets (NFPA competency matrix — the complex one)
- **Authoring** `skillSheets/{sheet-<n>}`: `{sheetNumber, titleEn/Ar, status(draft|review|approved|archived), docReference, nfpaStandard, courseCode, passThreshold, sortOrder, totalPoints(computed), version(int, optimistic-lock) + version(str, display — COLLISION, clean up), steps[]}`. Step: `{stepNumber, points, isCritical, sectionHeadingEn/Ar, taskEn/Ar, evaluatorNoteEn/Ar, cautionEn/Ar, mediaUrl, subItems[{en,ar,points}]}`. Editor: `SkillSheetView.jsx` (gated `skill_sheets_edit`).
- **Assessment** `SkillSheetAssess.jsx` (912 lines): group workflow (one sheet × a group, scored one at a time); phase machine `setup→select→score→result|sign→done→summary`; each step scored 0..points; signature capture (`SignaturePad`, reuse latest or fresh).
- **Scoring rules** `functions/skillSheetScoring.js` (authoritative CJS; ESM mirror `src/lib/skillSheetScoring.js` — drift test keeps them in sync; collapses to ONE shared TS module in Next.js):
  - **Any critical step below full = automatic fail** regardless of total.
  - `passed = totalAwarded >= passThreshold && !criticalFailed`.
  - **Re-test grade cap 60%** on attempt ≥2 (`RETEST_GRADE_CAP_PERCENT`).
  - **Max 3 attempts** (`MAX_SKILL_SHEET_ATTEMPTS`).
  - **No same-day re-test** — `Asia/Dubai` TZ day bucketing.
  - Status: pass→`awaiting_signature`; fail+exhausted→`completed_fail`; fail+remaining→`pending_retest`.
- **Roll-up** `SkillSheetMatrix.jsx`: rows=trainees (grouped by military-ID first digit, ORBAT-style), cols=approved sheets; cell status palette (completed_pass/awaiting_signature/pending_retest/completed_fail/none).
- **`skill_sheets_editor` role** = author-only (`skill_sheets`, `skill_sheets_edit`, `reports:create_basic`) — can author, NOT assess. Assessing = trainer/admin.

## 4. Data model (Firestore → Postgres targets)
| Collection (now) | Postgres target |
|---|---|
| `scdfTheoryAttempts` (auto) | `scdf_theory_attempts` |
| `scdfStatus/{traineeId}` | `scdf_status` (or materialized view of latest over attempts) |
| `scdfPracticalAssessments` (auto) | `scdf_practical_assessments` |
| `skillSheets/sheet-{n}` | `skill_sheets` (+ normalized `skill_sheet_steps` / `_sub_items` recommended for AI authoring/query) |
| `skillSheetResults/{traineeId}__{sheetNumber}` | `skill_sheet_results` PK `(trainee_id,sheet_number)` + `skill_sheet_attempts` child (≤3) |
| `config/scdf_settings` | per-org settings row |
- `skill_sheet_results.attempts[]` element: `{attemptNo, stepResults{step:pts}, totalAwarded, totalPossible, criticalFailed, rawPercentage, finalPercentage, gradeCapped, outcome, scoredAt, sheetSnapshot{...}, evaluatorComments, assessorUid, assessorName}`. **Keep `sheetSnapshot` per attempt** — scoring must stay reproducible after sheet edits.
- Signatures: Firebase Storage `skillSheetSignatures/{traineeId}/{sheetNumber}-{ts}.png` (2MB cap, tokenized URL) → Supabase Storage bucket.
- Theory question bank → real `theory_questions` **table** (add missing `difficulty`, `topic`, `media_url`, per-option correctness, question-type for future free-text) — tenant-scoped + AI-authorable.
- **Add `org_id` FK to EVERY table** (all single-tenant today).

## 5. Server logic (Firebase callables → Next.js server actions, service-role)
`submitScdfTheoryAttempt` (grade), `getScdfTheoryLatestResult`, `saveScdfPracticalRubric`, `saveSkillSheetAttempt` (**transactional**: reload sheet→snapshot, eligibility (3-cap/same-day/terminal), score, write, audit), `signSkillSheetResult` (**transactional** flip awaiting_signature→completed_pass), `createSkillSheet` (version=1), `updateSkillSheet` (**optimistic lock** on int version + changed-field audit diff), `deleteSkillSheet`. All bypass rules via admin SDK.

## 6. Non-trivial port items (watch)
1. `Asia/Dubai` same-day-retest — DB-side date bucketing or fixed-TZ util, not naive UTC.
2. Optimistic locking → `UPDATE ... WHERE version = $expected`.
3. Transactional integrity of `saveSkillSheetAttempt`/`signSkillSheetResult` — Postgres txn; eligibility re-check INSIDE it.
4. Signatures → Supabase Storage, keep 2MB cap + signed URLs + "reuse latest".
5. Embedded `sheetSnapshot` per attempt — do NOT normalize away.
6. Denormalized trainee fields on results (name/militaryId/batch/group) drive the matrix — decide join-vs-denormalize.
7. Trainee auto-redirect to `/scdf/theory` — reproduce in Next.js middleware/layout by role.
8. Clean up: stale rule collection names (`scdf_theory_results` etc. don't match real collections); display-vs-lock `version` collision; ESM/CJS scoring-lib duplication → one shared TS module.

## 7. Where AI attaches (per vision AI thesis; human-in-the-loop)
- **Question-authoring**: pipeline writes to `theory_questions` + `skill_sheets`/`skill_sheet_steps`; source docs in-repo (`SCDF_Theory_Test.docx`, `SCDF_Theory_Questions.xlsx`). Human review = the step/question editor UI. AI must populate step `points/isCritical/taskEn/taskAr/evaluatorNote/caution`.
- **Rubric grading of free-text**: attaches at `saveSkillSheetAttempt` (before `scoreNewAttempt`) — AI suggests `stepResults` for assessor confirmation. Free-text theory needs new question types in `theory_questions`.
- **Assessor-consistency checks**: over `skill_sheet_attempts` (carry `assessorUid/Name`, `finalPercentage`, `criticalFailed`, `scoredAt`); `SkillSheetMatrix` + audit_log are the read models; scheduled job flags outlier assessors.
