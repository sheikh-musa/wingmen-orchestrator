# COSEM OMR Paper-Exam — Trial Plan (2026-08-23)

Owner: Nazim (orch-console). Client: Muhammad Hariz, UAE **CDA** (Academy of Civil Defense).
Purpose: a concrete, at-source-verified plan to trial **THEORY testing via OMR paper exams** for the
NFPA 1010:2024 Firefighter-I cohort. Companion to `reports/cosem/cosem-scope-v2.md` (authoritative
scope). All references below are verified in `~/wingmen/projects/cosem-platform` at source, not assumed.

---

## 1. Objective

Prove the paper-OMR pipeline **end-to-end** — print A4 answer sheet (fiducials + QR + bubble grid) →
hand-fill → scan/photo → auto-read → auto-grade → results — on a real firefighter cohort, in time for
the trial **week of Sept 7** (real namelist Sept 8), with the new cohort starting **Aug 31**.

Two phases:
- **DRY RUN (before Sept 7):** full flow on a *sample* question set + *synthetic* trainees. Prove the
  pipeline works and shake out physical print/scan issues before any real data touches it.
- **REAL TRIAL (week of Sept 7):** the same flow with the real cohort bank + real namelist.

**Load-bearing fact (verified):** the OMR **answer sheet carries NO question text** — only `Q1..Qn`
labels and A/B/C/D bubbles (`src/modules/exams/paper/pdf.ts` `renderAnswerSheetPdf`). Scanning +
grading are therefore **fully language-independent**. Arabic only matters on the *reading* question
paper (see §5 item iv). This is what lets us de-risk the trial hard.

---

## 2. What already exists (verified at source — do NOT rebuild)

| Capability | Where | Status |
|---|---|---|
| Question bank CRUD | `createQuestion/updateQuestion/deleteQuestion` in `src/actions/exams.ts`; UI `dashboard/exams/bank` | EXISTS (admin-only, `exams.author`) |
| draft→approved gate | `setQuestionStatus`; `createExam` rejects any non-`approved` question | EXISTS |
| Build + publish exam | `createExam` (`publish:true`→`status:'published'`); UI `dashboard/exams/build` | EXISTS |
| Generate paper batch | `generatePaperBatch` in `src/actions/exams-paper.ts`; UI `dashboard/exams/paper` | EXISTS |
| Merge to one printable PDF | `renderBatchPdf` (question papers + per-sheet answer sheets) | EXISTS |
| Scan → auto-grade | `gradeScan`; UI `dashboard/exams/paper/scan` (camera or file upload) | EXISTS |
| OMR read (fiducials/homography/QR/bubbles, fails safe) | `omr-reader.ts`, `grade-scan.ts` | EXISTS |
| Results list + pass/fail | `dashboard/exams/paper` (scanned results), `analytics` | EXISTS |

Constraints confirmed in code:
- Question type is **`mcq_single` only** (`validation.ts` `QUESTION_TYPES`) — exactly one correct
  option. The CDA bank is 4-option single-answer (a/b/c/d + one ANSWER letter), so this **matches**.
- Generate/scan pages are **staff-only** (`admin`/`assessor`); bank + build are **admin-only**
  (`requireAuthoring`).
- `generatePaperBatch` today mints synthetic `STU-001…` refs (`student_ref`) — it needs only a
  **roster size (count)**, not login accounts. Paper grading is keyed by `sheet_code → version →
  exam`, wholly independent of `exam_assignments`/logins.

## 3. Inputs pinned from `requirements-20260823/` (verified)

- **Question bank format is a Word `.docx` ExamView export — NOT xlsx.** `Response 3_Theory_-
  Question Bank _ExamView_.docx` is a table: `REF/ID | LEARNING OBJ | NATIONAL STANDARD | ANSWER
  (letter) | ENGLISH (stem + a/b/c/d) | ARABIC`. **279 questions** (FF1001–FF1279), bilingual, each
  ref-ID'd + tagged to NFPA 1010:2024 clauses + a learning objective, **with an answer key**.
  → scope-v2's assumption of an *xlsx*/`parseWorkbook` importer is **corrected**: the real importer
  is a **Word-table** parser.
- **`Set A FF01-FF Theory Test.docx`** is a **100-question** test set drawn from that bank
  (`ID | ANSWER: x | ENGLISH | ARABIC`) — the likely shape of one exam paper.
- **Namelist (`Response 5`) is a SCANNED IMAGE PDF** — 3 pages of JPEG scans, **no text layer, no
  fonts** (verified via `pdffonts`/`pdfimages`). Columns (read off the scan): `No. | Service No.
  (رقم) | Rank (الرتبة) | Arabic name | English name | Signature`. Header: *"1144 NFPA 1010 …"*.
  → **A scanned PDF cannot be imported.** For the real run we need this as **xlsx/CSV** (or OCR it).
- The existing onboarding importer (`onboarding/import.ts`) is xlsx-only and **requires DOB +
  Emirates ID** — neither is on this roster, and the paper trial does **not** need login
  provisioning anyway. So the roster path for paper is a **light name+serviceNo intake**, not the
  heavy onboarding importer.

---

## 4. Timeline (working back from today, Aug 23)

| Date | Milestone |
|---|---|
| **Aug 23 (today)** | Plan drafted. Send Hariz the asks in §6. |
| **Aug 26** | Hariz confirms question-paper **language decision** (§5 iv) + whether the Sept cohort uses the 279-Q bank / 100-Q Set A. |
| **Aug 27–29** | Load a **sample set (~15–20 Qs)** via manual CRUD → approve → build+publish a sample exam. Build net-new **(i)** real-trainee binding (small). |
| **Sept 1–2** | Physical dress rehearsal: print, hand-fill by hand, scan on a real phone. Fix any print/scan issue. |
| **➡️ Sept 3 (proposed DRY-RUN date)** | Full end-to-end dry run, sign-off checklist §? below. Leaves a 3–4 day buffer before the real trial. |
| **Sept 7 (week of)** | REAL TRIAL begins (real cohort bank + real exam). |
| **Sept 8** | Real namelist uploaded (as xlsx/CSV) → sheets bound to real trainees → conduct + scan + grade. |

**Why Sept 3 for the dry run:** it is the earliest date that does **not depend on Hariz** (we already
hold the ExamView bank + Set A, so the sample set is self-served, and the dry run uses *synthetic*
trainee refs — see §5). It still leaves a **3–4 working-day buffer** before Sept 7 to fix anything the
physical print/scan cycle surfaces. Earlier is possible if binding + sample load finish sooner.

---

## 5. DRY RUN — step-by-step (before Sept 7)

Goal: prove the pipeline with a **sample question set** + **synthetic trainees**. Every step has an
explicit **PASS** bar.

**(a) Load sample questions into the bank.**
Route-around: **manual CRUD** ~15–20 questions copied from Set A (`dashboard/exams/bank` → *New
question*: stem, 4 options, mark the one correct, set status). Admin login (`exams.author`).
→ **PASS:** ~15–20 rows visible in the bank list.

**(b) Approve them.**
`dashboard/exams/bank` → set each to **approved** (`setQuestionStatus`).
→ **PASS:** all sample questions show **approved**; the *Approved* filter lists them.

**(c) Build + publish the sample exam.**
`dashboard/exams/build` → title, pass threshold (default 60), duration (paper is untimed by the app —
the room proctor times it), select approved questions → **Create & publish**. `createExam` re-checks
every question is `approved` server-side.
→ **PASS:** redirected to the exam page; exam shows **published**; it appears in the paper console's
exam dropdown (that page filters `status==='published'`).

**(d) Generate a paper batch.**
`dashboard/exams/paper` → pick the exam → mode **versioned** (reusable shuffled papers) → set versions
(e.g. 4) + student count (e.g. 10) → **Generate**. (`generatePaperBatch` mints `STU-001…` synthetic
refs — fine for the dry run.)
→ **PASS:** batch row appears with N versions + 10 sheets; a batch short-code shows.

**(e) Print the merged PDF.**
Same page → **Download** → prints question papers + 10 answer sheets as one PDF (`renderBatchPdf`).
Print on a normal office printer, **plain A4, 100% scale (no "fit to page")**.
→ **PASS:** printed sheets show 4 sharp corner fiducials, a crisp QR, and a clean bubble grid; QR not
clipped; corners not cut off.

**(f) Hand-fill the sheets.**
Fill bubbles by hand with a dark pen — deliberately include: 2 clean-pass sheets, 1 clean-fail, 1 with
a **double-marked** question (to exercise `ambiguous`), 1 photographed at a slight angle/poor light
(to exercise `unreadable`/deskew).
→ **PASS:** sheets filled per the test matrix above.

**(g) Scan / upload + auto-grade.**
`dashboard/exams/paper/scan` → **Camera** (phone, environment cam) or **Upload** a photo →
`gradeScan` reads + grades.
→ **PASS:** clean sheets return **Competent/Not-yet-competent** with correct score; the double-marked
one returns **ambiguous** (question left unanswered, not guessed); the bad photo returns a clear
**"retake"** message — **never a fabricated grade** (verified: `gradeScan` records `unreadable`/
`mismatch` and returns null score rather than guessing).

**(h) Review results.**
`dashboard/exams/paper` (scanned results list) + `dashboard/exams/analytics`.
→ **PASS:** every scanned sheet appears with thumbnail, sheet code, version, pass/fail; scores match a
**hand-computed** answer key for the sample set (do this reconciliation explicitly — it is the real
proof the OMR read is correct).

**DRY-RUN OVERALL PASS:** load→approve→build→print→fill→scan→grade→results completes with (1) scores
matching the hand key, (2) ambiguous + unreadable handled safely, (3) at least one full cycle done on a
**real phone camera**, not just file upload.

---

## 6. REAL TRIAL — step-by-step (week of Sept 7; real namelist Sept 8)

Same flow as §5, with real content and one added binding step.

1. **Load the real cohort bank.** Either the **`.docx` importer** (net-new ii, if built — preferred
   for the 100-Q Set A) or manual CRUD if the exam is small. Approve all.
2. **Build + publish the real exam** (`dashboard/exams/build`) — real title, the CDA-confirmed pass
   threshold + question count.
3. **Ingest the real namelist (Sept 8).** Hariz sends **xlsx/CSV** (name + service no. + rank);
   feed the light roster intake (net-new i). *If* only a scanned PDF is available, OCR/transcribe
   (~30–50 names is small).
4. **Generate the batch bound to real trainees** (net-new i): sheets carry each trainee's **name +
   service number**, not `STU-001`.
5. **Print** the merged PDF (or: app prints the language-neutral **answer sheets**, and the client's
   existing bilingual Set A doc is the reading paper — see §7 iv).
6. **Conduct** the exam in the room (proctor-timed), collect sheets.
7. **Scan + grade** (`dashboard/exams/paper/scan`).
8. **Review + export** results (`dashboard/exams/paper`, `analytics`, xlsx export).

**REAL-TRIAL PASS:** every real trainee's sheet grades correctly and traces to the right named person;
a results export can be handed to CDA.

---

## 7. Net-new build split (effort + dry-run impact)

Effort key: **S** ≈ ≤0.5 day, **M** ≈ 1–2 days, **L** ≈ 3+ days.

| # | Item | Effort | Dry-run blocker? | Route-around for dry run | When it's needed |
|---|---|---|---|---|---|
| **i** | **Answer-sheet ↔ real-trainee binding** — pass a `[{name, serviceNo}]` roster into `generatePaperBatch`, stamp into `paper_answer_sheets` (+ a `student_name` col), render name on the sheet. | **S** | **NO** | Dry run uses synthetic `STU-001…` (already the default). | **Fast-follow — before Sept 8** real run. |
| **ii** | **Question-bank importer** — parse the **Word `.docx` ExamView table** (not xlsx): split the English cell into stem + a/b/c/d, map the `ANSWER` letter → correct option, carry Arabic + REF-ID + NFPA clause. | **M** | **NO** | Manual CRUD of the ~15–20-Q sample set. | **Fast-follow** if the Sept exam uses the 100-Q Set A (manual entry of 100 bilingual Qs is error-prone). Else manual CRUD. |
| **iii** | **NFPA-standard / structured REF-ID / author fields** on theory questions (additive cols + form). | **S–M** | **NO** | Stuff REF-ID into the existing `topic`/`performance_criteria` field. | **Deferrable** unless CDA accreditation requires it on the trial record. |
| **iv** | **Arabic/RTL rendering on the printed QUESTION PAPER** — `pdf.ts` uses `StandardFonts.Helvetica` (Latin-only); it **cannot print Arabic**. True Arabic needs an embedded Unicode font + bidi/shaping (pdf-lib does not shape Arabic natively). | **M–L** | **NO (if routed around)** | **Print English-only app papers**, OR print the app's **language-neutral answer sheets** and hand out the **client's existing bilingual Set A doc** as the reading paper. | **Deferrable** — build in-app Arabic only if CDA insists on app-printed bilingual papers. |
| **v** | **Namelist intake for paper** — light `name+serviceNo` xlsx/CSV → item (i). NOT the heavy onboarding importer (which needs DOB+EID + provisions logins, neither needed for paper). | **S** (folds into i) | **NO** | Synthetic refs. | Before Sept 8, once Hariz sends the roster as xlsx/CSV. |

**Bottom line: NO net-new item blocks the dry run.** The whole dry run runs today's code + manual CRUD
+ synthetic refs. Items (i) + (ii)/(v) are the fast-follows that must land before the **Sept 8** real
run. (iii) + (iv) are deferrable pending client/accreditation answers.

---

## 8. What we need from Hariz — and by when

| # | Ask | Why | By when |
|---|---|---|---|
| 1 | **Question-paper language decision**: (a) app prints English-only papers, (b) app prints answer sheets only + you supply the bilingual reading paper (your Set A doc), or (c) you require app-printed bilingual papers. | Determines whether the Arabic-font net-new (iv) is needed. (b) fully de-risks the trial. | **Aug 26** |
| 2 | **Confirm the cohort bank**: does the Sept cohort use the 279-Q NFPA 1010:2024 bank / the 100-Q Set A we have, or a different set? Confirm the ANSWER column is the authoritative key. | Decides manual-CRUD vs building the `.docx` importer (ii), and exam size. | **Aug 27** |
| 3 | **Exam parameters**: questions per exam, **pass threshold** (60% vs 70%), retest/attempt policy, number of shuffled versions, expected cohort size. | Needed to build + publish the real exam. | **Aug 29** |
| 4 | **Real namelist as xlsx/CSV** (English name, service no., rank, Arabic name optional) — **NOT a scanned PDF** (the one supplied is image-only and cannot be imported). | Real-trainee binding for the sheets. | **format by Sept 1; data Sept 8** |
| 5 | **Accreditation fields**: does CDA require NFPA standard / REF-ID / author **on the exam record/report**? | Decides whether net-new (iii) is in-scope for the trial. | **Aug 29** |

---

## 9. Open decisions (for CTO / client — kept separate from the mechanical steps)

- **[Client + CTO] Arabic printed papers.** Recommended: for the trial, app prints the
  **language-neutral answer sheets**; the client's existing bilingual Set A doc is the reading paper.
  This sidesteps the pdf-lib Arabic gap entirely. Decide before committing to net-new (iv).
- **[CTO] Importer vs manual CRUD.** Build the `.docx` ExamView importer (net-new ii, ~M) only if the
  cohort exam is large (Set A = 100 Q ⇒ yes). Effort-vs-deadline call.
- **[Client] Pass threshold + attempt policy** for the theory paper exam (60 vs 70; retest cap). The
  app defaults threshold 60; confirm CDA's standard.
- **[Client] Accreditation metadata** (NFPA std / REF-ID / author) required on the trial record? Drives
  net-new (iii).
- **[CTO — DATA RESIDENCY, standing pre-live gate]** Real CDA trainees = **UAE government-adjacent PII**
  (names, service numbers, ranks). Per TENANT-RESIDENCY-001, the **cosem tenant's designated data
  store must be confirmed BEFORE the first real trainee write on Sept 8** — never "temporarily" in a
  shared project. Name the exact store + project ref (`docs/data-store-registry.md`) and verify the
  write-target silo before the real namelist goes in. This is a hard gate on the Sept 8 step.

---

*All code references verified at source on 2026-08-23 in `~/wingmen/projects/cosem-platform`. No
product code was modified in producing this plan.*
