# ADCDA Course-Administrator Engine — design proposal (V1)

**2026-07-05 · Nazim (orch-console) · operator directive #2324. Scope: automate the CA's work (batch schedule planning, plan-vs-actual coverage, change-request consequences, real-time visualization) inside the cosem-adcda app, on a path to a progressively-autonomous AI CA. Strategically this is opportunity ① growing its second arm: assessment engine + scheduling/coverage engine = the certification-body OS.**

## The architecture decision that matters: deterministic core, LLM shell

Course scheduling under constraints (lesson prerequisites/contingencies before exams, pumper/equipment pools, academy locations, instructor availability) is a **constraint-satisfaction problem** — deterministic, checkable, explainable. An LLM must NEVER be the scheduler: a hallucinated schedule that drops a contingent lesson before an exam means trainees sit a certification exam unprepared (firefighter competency = the checked-means-checked domain; same bar as assessment scoring). The LLM belongs at the EDGES: ingesting messy human documents, translating instructor requests, explaining consequences. The engine in the middle is pure code whose every output is verifiable.

**Answer to "is this too much for one agent": wrong unit.** One LANE (cc-cosem-adcda) owns the build; the RUNTIME is one deterministic engine + three narrow agent roles. "One big AI CA" is how you get plausible-but-wrong schedules; this decomposition is how you get an AI CA you can certify.

## Three layers

### 1. Data model (Firestore, cosem-adcda app — client store, residency-clean)
- `lessons` — catalog: duration, resource requirements (pumper/equipment set/location type), instructor qualification tags, **contingency edges** ("must precede exam X", "requires lesson Y first").
- `resources` — pumpers, equipment sets, locations (capacity, availability windows).
- `instructors` — availability, qualifications (reuses assessor entities where they overlap).
- `schedule_slots` — the PLAN: lesson × time × location × instructor × resources, per batch/squad.
- `coverage_log` — the ACTUAL: per session, covered / partial / skipped + reason. **This is the ground-truth table the whole system turns on** — contingency tracking is plan-vs-actual, and only instructors' end-of-session check-ins make it real.
- `change_requests` — instructor ask (verbatim), proposed mutation, computed consequence set, status (proposed/approved/rejected/applied), approver.
- Reuses the NFPA module's existing course/skill/sequence entities — the assessment app already models "ordered skills per course"; scheduling generalizes it.

### 2. Deterministic engine (pure TypeScript lib in the app — no LLM, fully unit-tested)
- **Validator**: schedule + constraints → violations (prereq order, resource double-booking, location conflict, exam-readiness per squad: all contingent lessons covered before each exam slot).
- **Ripple computer** (the killer feature): proposed change → full consequence set — what breaks, what must move, which squad loses exam-readiness, which resources free/collide. Powers the "instructors don't realize the far-reaching consequences" moment, in milliseconds, provably.
- **Scheduler**: generate/repair (greedy + backtracking over resource calendars is sufficient at one-academy scale; CP-SAT later only if real usage demands it — takalluf guard).
- **Drift differ**: coverage_log vs plan → drift report + repair suggestions (auto-replan of uncovered contingent lessons ahead of exams).

### 3. Agent roles (LLM, narrow, each independently trustable)
- **Ingester** (cc-cosem-adcda lane, batch-folder access per operator): timetables/Excel/Word/PDF → structured entities. **Extraction is human-verified before commit** (CA reviews a diff-style import screen) — extraction errors must not silently become constraints.
- **CA copilot** (in-app, later phase): instructor request in natural language → proposed mutation → engine computes ripple → presents consequences → CA approves → commit. **No schedule change affecting an exam path ever auto-commits** until the autonomy ladder (below) says so.
- **Coverage tracker**: end-of-session instructor check-in flow (borrow the tdu attendance UX patterns) → coverage_log.

### 4. Real-time visualization (app module)
Schedule board: calendar/Gantt lanes per location + resource + squad; constraint status coloring (green/amber/red); **ghost-preview overlay** — a pending change_request renders its ripple visually before anyone approves. Firestore realtime listeners make "realtime" free; the board is a new route in the existing PWA behind the CA role.

## The path to "purely AI CA" — an autonomy ladder, not a big bang
L0 ingest-and-visualize (AI reads, humans decide) → L1 AI proposes, CA approves everything → L2 auto-approve categories with proven ≥N-decision clean approval streaks (e.g. same-day room swaps) while exam-path changes stay human → L3 AI CA with human exceptions. Every decision instrumented; promotion between rungs is an evidence event (approval-rate data), not a vibe. This is the shipforge hook→concierge→subscription discipline applied to autonomy.

## Sequencing (fence-honest)
- **NOW → Jul 13 → director (~wk Jul 20): NOTHING lands in the app — the showcase is fenced.** Only prep that costs the showcase zero: operator shares the batch folder read-only; post-reset (Wed) the lane runs a SCOPING INGEST of one batch off-app → extraction-quality report + refined data model. Evidence before build.
- **Phase 1 (post-director → pre-blackout Aug 25):** data model + engine + read-only board + coverage check-ins. The board alone is the director follow-up wow. Engine ships with a cc-reviewer pass (exam-path logic = certification integrity, per the fleet's own precedent).
- **Phase 2 (post-blackout Oct+):** change-request copilot with ghost-preview + full drift loop. (Same window as NEA/TDU staging — the two showcase clients both feed the ① product.)
- **Phase 3:** autonomy ladder climbs on data.

## Governance notes
Batch folder = client data; extracted entities live in the adcda Firestore (client store) — TENANT-RESIDENCY clean; the folder itself reaches the lane read-only via the Studio (operator action). Exam-affecting engine logic requires cc-reviewer sign-off before any live batch uses it. Allocation: this rides the cosem 40→35% lane share; no new burn during the ration window.
