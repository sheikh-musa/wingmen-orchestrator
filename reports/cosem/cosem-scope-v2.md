# COSEM Assessment — Scope v2 (2026-08-23)

Supersedes v1's feasibility guesses with **at-source verification** of cosem-platform (`~/wingmen/projects/cosem-platform`, Next.js 16 + Supabase, exams module LIVE). Inputs: Hariz's 8 answers + 9 requirement files (`reports/cosem/requirements-20260823/`), op 15968-15989/15999. Owner: Nazim (orch-console). cosem-exams = supervised, reviewer=orch-console, lane down → `dev_group_send.sh cosem-exams` is the reply path.

## 🔴 DEADLINE + FIRST SLICE
- **New cohort Aug 31; trial THEORY testing via OMR PAPER exams ~week of Sept 7** (Hariz op 15988). Per-cohort question bank incoming.
- **FEASIBILITY = YES, mostly enable+configure.** The load-bearing risk — does paper-OMR (print → hand-fill → scan/photo → auto-read → auto-grade) exist? — resolves **POSITIVE**: it's built end-to-end in `src/modules/exams/paper/*` (`pdf.ts` printable A4 answer sheets w/ fiducials+QR+bubble grid; `omr-reader.ts` pure-TS fiducial/homography/orientation/QR/bubble read, fails safe; `grade-scan.ts` grades via the SAME `gradeTheory` engine) + `src/actions/exams-paper.ts` (`generatePaperBatch`/`renderBatchPdf`/`gradeScan`) + UI `src/app/dashboard/exams/paper/{page,scan}`. Deps: pdf-lib/qrcode/jsqr/sharp. NOT on-screen-only.
- **Trial = enable+configure:** load cohort questions into bank (manual CRUD, `exams.author`) → approve (draft→approved gate EXISTS) → build+publish exam → generatePaperBatch → print merged PDF → scan → review results.
- **Thin NET-NEW needed for a REAL cohort trial (small, additive):**
  1. **Answer-sheet ↔ real-trainee binding** — today `generatePaperBatch` mints synthetic `STU-001…` refs, not real roster identities. Tie sheets to the actual namelist. (small)
  2. **.xlsx question importer** — if the cohort bank arrives as Excel (reuse `parseWorkbook` from `onboarding/import.ts`; deterministic column-map, low-risk). Confirm format w/ Hariz.
  3. **NFPA standard / structured Question ID / author on THEORY questions** — additive columns+form (pattern already exists on skill sheets). Only if accreditation needs it on the trial.
- **Sent Hariz** (feasibility + these 3 asks + confirm paper-vs-onscreen + send bank). Awaiting bank + format. NEXT: concrete step-by-step + realistic dry-run-before-7th date → consolidated Musa update (deadline on track).

## FULL CAPABILITY MAP (EXISTS / PARTIAL / NET-NEW — verified at source)
- **Theory exam + paper-OMR** — EXISTS (mcq_single only; multi-select net-new).
- **Question bank** — PARTIAL: CRUD+approve+publish gates EXIST; xlsx question importer NET-NEW; NFPA/structured-ID/author fields on theory NET-NEW (additive); "Data Matrix" metadata table mostly NET-NEW (the existing `matrix.ts` is a derived pass/fail competency grid, not metadata).
- **Exam conduct** — proctor/session flow EXISTS (`exam-sessions.ts`, server timer, screen-exit logging); attempt_no pervasive but `DEFAULT_MAX_ATTEMPTS=0` (unlimited default, org-configurable) — hard 1/2/3 model NET-NEW refinement; **appeals NET-NEW** (zero matches).
- **Practical skill-sheets** — EXISTS + RICHER than v1 said: `nfpa-scoring.ts` = weighted partial-credit per step (NOT 0/1), critical GO/NO-GO auto-fail, per-sheet threshold, 60% retest cap; sheets carry nfpa_standard/doc_reference/course_code. Gap: per-attempt **remarks** on the 3-attempt track (minor net-new).
- **Roles** — base admin/assessor/student/verifier/member + functional roles (trainer/tc/regulator/hr/ops/finance/onboarding_clerk/skillsheet_author/course_instructor/course_supervisor). Student+Verifier EXIST; **Proctor/Evaluator split NET-NEW** (small sub-type flag — today one `assessor` does both); Assessment Author PARTIAL (capability exists, not a role); Course Instructor/Administrator PARTIAL (functional roles exist but scoped to roster attendance only — the scheduling/sign-off duties NET-NEW).
- **Namelist/roster import** — PARTIAL: xlsx people-importer EXISTS (`onboarding/import.ts`); "create test group from official namelist" NET-NEW (reuses importer); PDF namelist ingestion additional (xlsx-only today; ID-OCR helpers exist).
- **Appraisal/attitude scores** — NET-NEW (zero matches). **Transcripts** — NET-NEW. **Remediation** — NET-NEW.
- **Results export** — xlsx EXISTS (`results-export.ts`); PDF-table (indiv/group) NET-NEW. **Analytics** — PARTIAL (thin; question-level NET-NEW).

## v1 CORRECTIONS
- Practical scoring is weighted partial-credit + critical auto-fail + 60% retest cap — NOT the "met/not-met 0/1" v1 stated. "Steps-scored" is largely ALREADY present; only per-attempt remarks missing.
- No hard practical attempt cap today (`DEFAULT_MAX_ATTEMPTS=0`, not `MAX_PRACTICAL_ATTEMPTS`); 1/2/3 model is net-new.
- "Data Matrix" ≠ the existing competency matrix (shares only the word).

## OPEN (Hariz)
- Question bank for the cohort (+ Excel format/columns). Confirm paper (print+scan) vs on-screen. Deadline date lock for week-of-Sept-7.

## RECOMMENDED MVP (post-deadline, beyond the trial)
Phase 0/1 per v1 still holds — accreditation bank metadata (NFPA/IDs/Data-Matrix/approve) + Proctor/Evaluator conduct loop — now with verified reuse; the trial IS the wedge into Phase 1. Appeals/transcripts/attitude/remediation/course-instructor-state-machine/GL = later phases.
