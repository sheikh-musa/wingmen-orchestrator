# cosem-platform — student-exam bug fix brief (op#5212)

**Repo:** `~/wingmen/projects/cosem-platform` (Next.js, src/app). **From:** cc-orchestrator (hub).
**Operator bug:** assessment full flow (admin assignment → assessor Start → student sign-in) → student page WHITE-SCREENS on load, then the countdown shows "0:00 — time's up — submitting…". Wants a clean full-flow demo.

Root cause (diagnosed, file:line confirmed): the deadline is already ≤ now when the student page mounts (Bug 2), which trips a TDZ crash in the timer effect (Bug 1). Two faces of the same load-time-expired scenario.

## Student exam-attempt flow (context)
- `src/app/dashboard/exams/take/[examId]/page.tsx` — server entry (auth `requireModule('exams')`, loads exam/questions/live session → renders `ExamRunner`).
- `run.tsx` — `ExamRunner`: waiting-room ↔ started; computes `deadlineMs`, hands to `TakeExam`.
- `take.tsx` — `TakeExam`: quiz UI + countdown + submit. **← both bugs surface here.**
- `src/modules/exams/types.ts:114-116` — `sessionDeadlineMs()` = `start_time + duration_minutes*60_000` (arithmetic is CORRECT — not a units/tz/NaN bug).
- `src/actions/exam-sessions.ts:81-84` — assessor `startSession` stamps `exam_sessions.start_time = now()` (ONE shared start for all students; participant upsert in `pollExamSession` :151-163).

## BUG 1 — white-screen crash (FIX ALWAYS)
`src/app/dashboard/exams/take/[examId]/take.tsx:125-139`: `tick()` is invoked at line 135 BEFORE `const id = setInterval(...)` at line 136. On a future deadline the first tick sees `left>0` and skips the `if`, never touching `id`. But when `deadline ≤ now` at mount, the first tick enters `left<=0` → evaluates `clearInterval(id)` while `id` is in the TDZ → `ReferenceError: Cannot access 'id' before initialization`, thrown inside useEffect → with NO error boundary anywhere under `src/app/**`, React unmounts the whole tree → white screen. (The "submitting…" is just the `remaining===0` UI; `doSubmit()` on :132 never runs because the throw on :131 precedes it.)

**Fix (reorder + `let` binding):**
```ts
useEffect(() => {
  if (!hasTimer) return
  let id: ReturnType<typeof setInterval>
  const tick = () => {
    const left = Math.max(0, Math.round((deadline - Date.now()) / 1000))
    setRemaining(left)
    if (left <= 0) { clearInterval(id); void doSubmit() }
  }
  id = setInterval(tick, 1000)
  tick()                       // id is initialized now
  return () => clearInterval(id)
}, [hasTimer])
```
ALSO add an error boundary so no page can ever white-screen again: `src/app/dashboard/error.tsx` (a `'use client'` `error.tsx` with a friendly "something went wrong / reload" + reset()).

## BUG 2 — deadline expired at load (per-student clock)
The deadline is anchored to the single shared `session.start_time` (stamped once at assessor-Start), with no per-student clock and no guard against joining an already-elapsed window. A student who signs in after the assessor started (the operator's flow) — or with a short duration — loads to `deadline ≤ now` → `remaining` inits to 0 (take.tsx:55-61) → sticky "0:00 time's up". Students already in the waiting room at Start are fine (fresh future deadline).

**Fix — PER-STUDENT CLOCK (the operator's default for the demo; he may still choose shared-window — see note):**
- Add a `started_at timestamptz` to `exam_session_participants` (migration, additive nullable), set it when the student actually STARTS their attempt (first load of TakeExam / on the existing participant upsert path in `pollExamSession` — set once, do not overwrite).
- Compute the student's `deadline = participantStartedAt + duration_minutes` instead of the shared `session.start_time + duration`. Wire it through `run.tsx:58` → `take.tsx`.
- GUARD regardless: if `deadline ≤ now` at mount, render a clean "exam window closed / session ended" state — NEVER a silent empty auto-submit or a crash.

**PRODUCT NOTE (do not silently change real-exam semantics):** shared-window (everyone ends together, like an invigilated paper) vs per-student (each gets full time from when they start) is a real assessment-policy choice. Default PER-STUDENT for the demo, but keep it a clear, single-switch config (e.g. a per-exam or per-schedule flag) so shared-window remains selectable. Hub is confirming the operator's preference; build per-student, make the switch obvious.

## Deliverable
- Both fixes on a feature branch. Migration additive/nullable (no destructive change). 
- TEST THE FULL FLOW end-to-end: admin creates assignment → assessor opens+starts session → student signs in AFTER start → timer shows the FULL remaining duration counting down (not 0:00) → answer → submit → result. Plus: a student loading an already-closed window sees the "closed" state, not a crash. Screenshot the working student timer.
- `next build` + typecheck green. Report branch/SHA + screenshots to hub (cc-orchestrator) for review + deploy. Do NOT self-deploy. Note the deploy target (cosem-platform was cloned from cosem-adcda → likely Firebase Hosting; confirm).
