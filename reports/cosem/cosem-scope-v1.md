# COSEM Assessment Platform — Implementation Scope v1 (first pass, for review)

**Status:** DRAFT for operator + client (Hariz) review. This is a structured, buildable *first pass* — **not a final spec**. Where the source spec is thin or absent, this doc flags the gap rather than inventing detail. See §5 (Open Questions) and §6 (Assumptions).

**Sources**
- Client spec: `reports/cosem/CSOEM-Assessment-App-Feedbacks-20260822.pdf` (Hariz, ECS fire-fighting skills certification, NFPA standard) — a role-structured change/upgrade list for the *existing* assessment app.
- Addendum (Hariz, after): *"an additional pointer for admin whereby they have to upload the official namelist that was created by the Student Affairs Section to create the test group."*
- Existing app: repo `cosem-platform` (Next.js 16 + Supabase), live demo `cosem-platform-demo.vercel.app`, DB = SG Supabase `ywrpttpxwfcoodovxhsr` (synthetic demo data).
- Existing-state mapping below was **verified against the current `main` branch code** (not spec/demo alone). Verified 2026-08-23.

---

## 1. Context — this is an UPGRADE, not greenfield

The assessment app already exists and ships a substantial exams module. Confirmed present in code today (`src/app/dashboard/exams/*`, `src/actions/*`, `src/modules/exams/*`):

- **Roles** (per-org, permission-matrix-tunable): `admin`, `assessor`, `student`, `verifier`, `member`. Access = intersection of org module-enablement × per-role matrix. No rebuild to re-tune a role.
- **Question bank** (`/exams/bank`) — manual CRUD, admin-only via `exams.author` capability, with a `performance_criteria` field per question.
- **Exam build / sets** (`/exams/build`, `[examId]`) — assemble questions into an exam.
- **Theory OMR** — paper bubble-sheet capture already built (`/exams/paper/scan`, `src/modules/exams/paper/omr-reader.ts`, QR + fiducial corner marks).
- **Practical skill sheets** (`/exams/skillsheets`) — authoring, A/B/C-style sets, per-criterion met/not-met scoring (0/1), a competency **matrix** view, capture/session/sign flows.
- **Attempts** — an `attempt_no` model exists; `MAX_PRACTICAL_ATTEMPTS` constant governs practical retests (pending_retest → completed_fail).
- **Verifier flow** (`/exams/verify`) — second-signature endorsement of captured results.
- **Assignment** (`/exams/assign`) — assign assessors / exams.
- **Namelist import** — `/dashboard/onboarding/import` already parses `.xlsx` workbooks (`parseWorkbook`, column auto-detect/mapping, template download) to onboard *people*.
- **Analytics** (`/exams/analytics`) — exists but thin (attempt counts only).

**What this spec adds** is largely: (a) 3 new roles, (b) a formal Proctor/Evaluator split of the assessor, (c) an appeals system (net-new), (d) transcripts + sign-offs (net-new), (e) richer question/skill metadata + a "Data Matrix" replacing the competency matrix, (f) question-level analytics, (g) AI-assisted bank ingestion from Excel, and (h) extending the existing namelist importer to create **test groups** (the addendum). Most items are *upgrades of existing surfaces*, which lowers risk — but several (appeals, transcripts, new roles, AI ingestion) are genuinely new subsystems.

> **Note (prior removal):** an earlier build **removed** AI question-authoring in favour of manual CRUD (per build memory). The spec's "Can A.I. assist author to capture data from the Excel submission" **re-introduces** an AI path. Confirm intent and scope before rebuilding it (see §5).

---

## 2. Per-role changes

Legend: **NEW** = net-new capability/role · **UP** = upgrade of an existing surface · **GAP** = spec incomplete.

| # | Role | Status today | This spec |
|---|------|--------------|-----------|
| 1 | Admin (ECS / authorised personnel) | Exists (`admin`, full) | Mostly UP + some NEW (results capture precision, appeals ack, results analytics, verifier reports) |
| 2 | Assessment Author | **No such role** | **NEW role** |
| 3 | Assessor (Proctor / Evaluator) | `assessor` exists, single type | UP — **split into 2 sub-types** |
| 4 | Verifier | Exists (`verifier`, endorse/sign) | **GAP** — client will send a separate doc |
| 5 | Student | Exists (`student`) | UP — results-progression overview, appeals |
| 6 | Course Instructor | **No such role** | **NEW role** |
| 7 | Course Administrator | **No such role** | **NEW role** |

### 1. Admin — ECS & authorised personnel
Current practice: assign assessment to approved personnel. The admin role already exists with full access; most of this is tightening/extending existing admin surfaces.

- **Approve Questions/Skills** — *UP.* Add an explicit approval state to questions & skill sheets (draft → approved), so only approved items enter test sets. (Today authoring is admin-gated but there's no formal approve gate.)
- **Choose chosen Set for test** — *UP.* Set-selection exists via exam build/assign; formalise "pick the active set for a test".
- **Collate results** — *UP.* Results collation exists (matrix/export); extend to the new attempt/appeal model.
- **Assign** — *UP.* "Assign Tests Sets to Group, Evaluator/Proctor to the question group" **combined with assign-to-students in one action** ("COMBINE ASSIGN TO STUDENTS & ASSIGN TO TEST TOGETHER"). Today assignment exists but likely as separate steps — merge into a single assign flow (group + set + proctor/evaluator + students).
- **Capture Results**:
  - Theory OMR bubble sheets — *UP* (already built).
  - Practical **steps-scored** — *UP/partly NEW.* Per-criterion met/not-met exists; spec marks practical steps-scored as **\*NEW\***, implying a richer per-step capture than the current 0/1. Confirm mechanics (§5).
  - Attempt **1/2/3 separated, with an appeal status on attempt 3** — *NEW refinement.* Today `attempt_no` exists but there's no per-attempt result separation UI nor an appeal state.
  - **PDF downloadable in table form** — *NEW* (results as downloadable PDF table).
- **Acknowledge of Appeals Submission** (appeal on question/skill + appeal on results) — **NEW** (appeals system, §3).
- **Acknowledgement of All results** — *UP/NEW* (an ack step across results).
- **Acknowledgement of Transcript** — **NEW** (transcripts, §3).
- **Results Analytic \*NEW\*** — summary of all tests conducted + PDF-downloadable table. **NEW** (current analytics is thin).
- **Verifier Reports \*NEW\*** — status completion of reports + sign-off by verifier/test coordinator. **NEW** (depends on the Verifier doc, §4).

### 2. Assessment Author — **NEW ROLE**
Current practice: they design assessment items on the agency template; ECS uses the template for the assessment. Function: upload/create questions per accreditation requirements. Needs a new role (likely a scoped role or a capability bundle on top of `member`; today only `admin` authors).

- **Test Bank — Theory** (*UP of the current Question Bank*):
  - **AI-assist to capture data from the current Excel submission to build the bank** — **NEW** (feasibility + scope open, §5).
  - Add **NFPA Standard** field — *NEW field.*
  - Add **question author name** — *NEW field.*
  - Add **Question ID = `Course Code-Question Bank No`** — *NEW field* (structured, generated ID).
  - Replace competency Matrix with a **Data Matrix on all items**: references, author name, date created/reviewed — *UP* (the current matrix view is the visual precedent; the *content* changes).
- **Theory Test Sets** ("similar to Build an Exam") — *UP.* Add: **auto-create sets** from the required number of questions, referencing NFPA Standard + Performance Criteria. (Manual build exists; auto-composition by NFPA/criteria is NEW.)
- **Question Analytics — Theory** — **NEW**: times-used count + question feedback (by Verifier / Student / Proctor).
- **Test Bank — Practical** (*UP of current Skill Sheets*):
  - **AI-assist from Excel** — **NEW** (same as theory).
  - Add **Performance Criteria**, **question author name** — *NEW fields* (criteria partly exists).
  - **Document Reference → Skill ID = `Course code-Question Bank No`** — *NEW field.*
  - Same **Data Matrix** (references, author, date created/reviewed) — *UP.*
- **Practical Test Sets** (Skill Sheet → Practical Set) — *UP + NEW*: auto-create sets by required count referencing NFPA + Performance Criteria.
- **Question Analytics — Practical** — **NEW** (times-used + feedback).

### 3. Assessor — split into **Proctor / Evaluator** sub-types (*UP*)
Today there's a single `assessor` role (the codebase already carries proctor/evaluator vocabulary and a `[examId]/proctor` route, but not a formal 2-type role split). Shared: **maintain dashboard**; **quick actions showing only the assigned group + assessment being tasked**.

- **Proctor** (covers Theory): view questions; **write feedback on questions**; **start/end group session** with captured **timestamp / cancelled-session / request-remarks**. — *UP* (session-conduct exists; add end/cancel/remarks + timestamps).
- **Evaluator** (covers Practical): add **question feedback**; **assigned-task only** — *remove "conduct a practical"* (evaluator does not initiate, only executes assigned tasks). — *UP.*

Implementation likely = one `assessor` role with a sub-type flag (`proctor` | `evaluator`) driving dashboard + capabilities, rather than two separate roles. Confirm preference.

### 4. Verifier — **SPEC GAP**
Spec text verbatim: *"Will update in another document."* A `verifier` role already exists (endorses/signs captured results, `/exams/verify`). **We cannot scope the verifier changes until Hariz sends the promised doc** (§5). The Admin's "Verifier Reports" item (status completion + sign-off) depends on it too.

### 5. Student (*UP*)
- **My Assessments: Theory** — start button **only activates once the proctor starts the test** — *NEW gating* (ties to proctor session start).
- **My Assessments: Practical** — data analytic on skills performed, **reviewed wrong items + the correct steps** — *NEW* (per-skill review with correct-step guidance).
- **My Results** — *UP.*
- **My Appeal** — appeal on question/skill + appeal on results — **NEW** (appeals, §3).
- **Overview dashboard on results progression** — **NEW composite dashboard**:
  - Theory — maintain, but with **3-attempt status and scores**.
  - Practical — **3-attempt status**; instead of skill-based, **module-based** summary (how many skills pass/fail).
  - Appraisal — **4 different categories** (categories undefined, §5).
  - Student Affairs — **attendance scores + attitude scores** (attendance module exists; **attitude score is NEW**).
  - Transcript — for acknowledgement and sign-off (§3).

### 6. Course Instructor — **NEW ROLE**
Read/acknowledge-oriented role over a course's test lifecycle:
- Overview of test dates; overview of revision test status; overview of review test status; overview of group results.
- **Acknowledgement of skills/theory covered** — *gates* the Course Administrator to proceed with the test-dates application to ECS.
- Acknowledge of transcript.

### 7. Course Administrator — **NEW ROLE**
Owns the test-scheduling request lifecycle:
- **Apply for test dates**; overview of theory/skills covered along the course.
- **Sign off revision dates** assigned to test → lets a trainee start an attempt.
- **Sign off review dates** assigned to test → lets a trainee proceed to attempt 2/3.
- Acknowledge appeals submission; acknowledge all results of all attempts; acknowledge overall results.

> Roles 6 & 7 introduce a **scheduling + sign-off workflow** (apply → instructor acknowledges coverage → admin/ECS approves dates → revision sign-off unlocks attempt 1 → review sign-off unlocks attempts 2/3). This is a **new cross-role state machine**, not just two dashboards — scope it as one workflow (§3).

---

## 3. Cross-cutting new capabilities

These span multiple roles; build them as shared subsystems.

1. **OMR results capture (two modes).**
   - *Theory* — bubble sheets: **already built** (`omr-reader.ts`, scan + QR/fiducials). Extend to feed the attempt-1/2/3 model + PDF table export.
   - *Practical* — **steps-scored**: current per-criterion 0/1 exists; the spec marks steps-scored **NEW**, implying richer capture (ordered steps, per-step pass/fail, possibly partial/critical-step logic). **Mechanics undefined** (§5).

2. **Attempt 1/2/3 model + attempt-3 appeals.** Formalise a max-3-attempt model, per-attempt status & scores, with an **appeal status specifically on attempt 3**. Attempts 2/3 are gated by Course-Administrator "review date" sign-off (role 7). Build on the existing `attempt_no` column; add attempt-status + gating state.

3. **Appeals workflow (net-new).** Two appeal types: **on a test question/skill** and **on test results**. Submitted by students; acknowledged by Admin *and* Course Administrator. Needs: appeal entity, states (submitted → acknowledged → resolved), linkage to question/skill or to a result/attempt, and audit. No appeals code exists today.

4. **Analytics / Data Matrices (net-new depth).**
   - *Question Data Matrix* — per item: Question ID (`Course Code-Question Bank No`), author name, NFPA references, date created/reviewed. **Replaces** the competency matrix.
   - *Question analytics* — times-used count + feedback (Verifier/Student/Proctor), theory & practical.
   - *Results analytics* — summary of all tests conducted, PDF-downloadable table.

5. **Transcripts + sign-offs (net-new).** A per-trainee/per-course transcript that Admin, Course Instructor, and Course Administrator **acknowledge/sign off**. No transcript code exists today. Needs a transcript entity + multi-party sign-off + PDF render (align with the existing cosem-adcda "authoritative printable PDF" pattern — faithful layout, no post-generation edits).

6. **AI-assisted bank ingestion from Excel (net-new / re-introduced).** "AI assist author to capture data from the current Excel submission to build the [theory + practical] bank." An earlier build removed AI authoring; this re-adds an AI ingestion path. Needs: a sample of the **current Excel submission format** and a feasibility call (deterministic parse + column-map vs. LLM extraction). Suggest an ingest pipeline that maps Excel → structured draft questions/skills for **human review before approval** (never auto-approve into the live bank). Feasibility/scope open (§5).

7. **Namelist → Test Group importer (the addendum).** Admin uploads the **official namelist created by the Student Affairs Section** to **create the test group**. Strong reuse: the onboarding importer already parses `.xlsx` with column auto-detect/mapping. Extend it to a **test-group creation** flow (match names to existing onboarded trainees; create a group/cohort; surface unmatched rows). **Namelist file format from Student Affairs is undefined** (§5). Reuse `parseWorkbook`/`ColumnMapping`; do **not** fork.
   - *Residency/PII note:* keep to synthetic demo data until go-live; real trainee PII (military/Emirates ID) is a residency + security gate (TENANT-RESIDENCY-001) — flag before any real namelist is loaded.

---

## 4. Proposed phasing / MVP

The whole thing is large (3 new roles, appeals, transcripts, analytics depth, AI ingestion, scheduling workflow). Suggested build order — **each phase shippable/demo-able on its own**:

- **Phase 0 — Foundations & metadata (low risk, high leverage).**
  - Question/Skill new fields: NFPA Standard, author name, structured **Question ID / Skill ID** (`Course Code-Question Bank No`), date created/reviewed, references. Additive migrations (psycopg).
  - Replace competency matrix view with the **Data Matrix**.
  - Formal **approve** state on questions/skills.
  - *Ships:* a richer, accreditation-ready question/skill bank.

- **Phase 1 — Assessor split + session conduct + student start-gating.**
  - Proctor/Evaluator sub-type; per-type dashboard + quick actions; proctor start/end/cancel session + timestamps/remarks; student theory start gated on proctor start; question feedback capture.
  - *Ships:* a clean conduct loop for a live theory + practical test.

- **Phase 2 — Attempt 1/2/3 model + results capture + PDF exports.**
  - Formal 3-attempt status & scores (theory OMR + practical steps-scored — pending §5 on steps mechanics); per-attempt separation; PDF table exports; module-based practical summary.
  - *Ships:* the core results engine the certification needs.

- **Phase 3 — Scheduling & sign-off workflow (roles 6 & 7).**
  - Course Administrator apply-for-dates; Course Instructor coverage acknowledgement gate; revision sign-off (unlock attempt 1) + review sign-off (unlock attempts 2/3); acknowledgements.
  - *Ships:* the end-to-end "who unlocks what" state machine.

- **Phase 4 — Appeals + transcripts.**
  - Appeals (question/skill + results) with Admin + Course-Admin acknowledgement; transcripts with multi-party sign-off + PDF.

- **Phase 5 — Analytics depth + AI ingestion + Assessment Author role.**
  - Question analytics (usage + feedback), results analytics summary; Assessment Author role; AI-from-Excel ingestion (pending feasibility, §5).

- **Deferred / blocked:** **Verifier** changes (Phase TBD — blocked on Hariz's separate doc, §4); **Appraisal 4 categories** and **attitude scores** (need definitions, §5).

**Suggested first shippable slice (MVP):** **Phase 0 + Phase 1** — the metadata upgrade (accreditation-ready bank + Data Matrix + approve gate) plus the Proctor/Evaluator conduct loop with student start-gating. It is self-contained, demo-able, mostly upgrades of existing surfaces (lower risk), and unblocks the certification's day-to-day flow without waiting on the open questions. **Confirm this MVP steer with Hariz** (§5).

---

## 5. OPEN QUESTIONS FOR THE CLIENT (Hariz)

Precisely what's needed to complete the scope. Grouped by what each blocks.

1. **Verifier role doc (blocks role 4 + Admin "Verifier Reports").** Spec says "will update in another document." Please send it. Until then the verifier changes and the verifier-reports sign-off are unscoped.
2. **Appraisal — the 4 categories (blocks the student overview dashboard).** What are the 4 appraisal categories, who scores them, and against what scale?
3. **AI-from-Excel ingestion (blocks Phase 5).** (a) Is AI extraction actually wanted, or is a deterministic column-mapped importer acceptable? (b) Please share a **sample of the current Excel submission format** (theory + practical) so we can assess feasibility and design the mapping. Note: we removed an earlier AI-authoring path — confirm the intent to re-introduce it.
4. **Practical "steps-scored" mechanics (blocks Phase 2 practical).** Beyond the current met/not-met per criterion — do you need ordered steps, per-step pass/fail, critical (auto-fail) steps, partial credit, timing? A sample official skill sheet with the scoring rules would let us build it exactly.
5. **Student Affairs namelist format (blocks the addendum test-group importer).** Please share a **sample of the official namelist file** Student Affairs produces (columns, format, one file). Also: how should a namelist row match an existing onboarded trainee (military/Emirates ID? name?), and what should happen to unmatched rows?
6. **Attitude score (blocks Student Affairs section of the student dashboard).** Who assigns "attitude scores," on what scale, and where captured?
7. **Roles vs. sub-types preference.** Should Proctor/Evaluator be one role with a sub-type flag, and should Assessment Author / Course Instructor / Course Administrator be full roles or capability bundles? (Affects the permission model.)
8. **Priority / MVP steer.** Is the Phase 0 + Phase 1 MVP the right first slice, or is a different subset more urgent for the certification calendar? Any hard deadline (e.g. a course cohort's test window)?

---

## 6. Assumptions (all correctable)

- **A1 — Single tenant for now.** Changes target the existing COSEM/ECS tenant on the SG demo DB; built generalised (no ECS-isms in code) per the multi-tenant product doctrine, but not validated against a 2nd tenant in this pass.
- **A2 — "Set" = existing exam/skill-sheet set concept.** The spec's "chosen Set for test" maps to the current exam-build / skill-sheet-set surfaces.
- **A3 — Max 3 attempts** is a hard cap (attempts 1–3), with appeal available on attempt 3. Practical already carries a `MAX_PRACTICAL_ATTEMPTS`; we assume theory adopts the same cap.
- **A4 — Data Matrix replaces (not augments) the competency matrix.** The spec says "instead of having competency Matrix, shall have Data Matrix"; we treat the old matrix view as the visual precedent, swapping its content.
- **A5 — Proctor = theory, Evaluator = practical**, as a sub-type of the existing `assessor` role rather than two brand-new roles (pending §5.7).
- **A6 — Assessment Author, Course Instructor, Course Administrator are new roles/capability bundles** layered on the existing role/permission-matrix machinery; no bespoke auth system.
- **A7 — Namelist importer reuses the onboarding `parseWorkbook`/column-mapping infra**, extended to create a test group; not a new parser.
- **A8 — AI ingestion produces human-reviewed drafts**, never auto-approving into the live bank (approval stays an admin gate).
- **A9 — Transcripts + generated results follow the cosem-adcda "authoritative printable PDF" pattern** (faithful layout, no post-generation edits, rendered directly to PDF).
- **A10 — All work stays on synthetic demo data** until a residency/security gate clears real trainee PII (military/Emirates ID) for go-live.
- **A11 — Migrations applied via direct psycopg** (additive `add column if not exists`), never `supabase db push`, per the established cosem deploy pattern.
- **A12 — "PDF downloadable in table form"** means a server-rendered tabular PDF export of results, reusing existing export plumbing (`results-export.ts` / `archive-export.ts`).
