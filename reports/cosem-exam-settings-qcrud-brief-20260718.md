# cosem-platform — exam settings + question CRUD + batch upload (op#5246/5254)

**Repo:** `~/wingmen/projects/cosem-platform` (Next.js). **Base:** off `fix/exam-per-student-clock-op5212` (inherits the verified timer/two-mode fix). **New branch:** `feat/exam-settings-qcrud`. **From:** cc-orchestrator (hub).

Operator greenlit the full set (op#5254 "lets do them all") + question management. Design pipeline (design-review, bilingual EN/AR, ihsan bar), review + tests. Do NOT self-deploy; report to hub.

## PART A — Exam settings (per-exam config; admin creates, assessor can override where noted)
Add a settings model + admin exam-config UI + assessor conduct-time overrides:
1. **TIMING** — default `duration_minutes` (admin); **assessor can override at conduct** → reflects on the student's clock (wire into the existing two-mode deadline). Late-join / grace window for proctored sessions (how long after Start a student may still join).
2. **SCORING** — configurable `pass_mark_pct` per exam (currently hardcoded 60%). Score-reveal policy: immediate vs after assessor review. Show-correct-answers policy: never / after submit / only after the window closes.
3. **ATTEMPTS** — max attempts (1 / N / unlimited) + retake cooldown.
4. **QUESTIONS (integrity)** — shuffle question order + shuffle answer options (there's an existing shuffle seed); **draw N random questions from a larger bank** (sample N-of-M so no two students get the same paper).
5. **INTEGRITY** — mode per exam: proctored (assessor session) vs self-paced; **configurable screen-exit limit** (currently hardcoded 3 = auto-fail) + require-fullscreen toggle. (Respect the mode-gating from the fix: auto-fail unproctored only.)
6. **AVAILABILITY** — exam open/close window (when it's takeable).
Sensible defaults for every setting (so existing exams keep working). Migration additive/nullable; confirm the DB target is non-prod for any LOOK.

## PART B — Question CRUD (admin)
- **Manual question builder:** question text + N answer options (add/remove), select the CORRECT option, and a **performance-criteria** field = **pick-or-type combobox** (reuse existing criteria OR add new — mirror the POS category-combobox pattern). Default free-form; if a fixed criteria list per standard (e.g. NFPA JPRs) exists/should, wire the dropdown source + flag hub.
- **Full CRUD:** create / **edit every question** / delete, with a manage-questions list/table view (search, filter by criteria). Guard edits to questions on a LIVE/in-progress exam sensibly (warn or version, don't silently change a paper mid-attempt).
- Validation: at least 2 options, exactly 1 correct, non-empty text.

## PART C — Batch upload (template)
- **Downloadable template** (CSV and/or XLSX) with columns: question, option A–D(+), correct option, performance criteria (+ optional: marks, standard/topic).
- Upload → **validate** (row-level errors surfaced) → **preview** the parsed questions → confirm → commit. Never partial-commit a bad file (all-or-nothing or clear per-row skip ledger). Idempotency/dedup so a re-upload doesn't duplicate.

## Deliverable
- All three parts on `feat/exam-settings-qcrud`. Migration(s) additive/nullable (no destructive change; existing exams/questions keep working with defaults). `next build` + typecheck + tests green. Screenshots of: an exam-settings form, the question builder (with performance-criteria pick-or-type), the manage-questions list, and the batch-upload preview.
- Report branch/SHA + screenshots to HUB (cc-orchestrator) for design+review gate. Do NOT self-deploy. Flag any product decisions (criteria-list source, edit-live-exam policy, template columns).
