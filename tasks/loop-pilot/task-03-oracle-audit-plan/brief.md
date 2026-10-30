# Task: oracle audit plan (eligibility + sample size)

## Context

hifz-companion's 30-day trust-decay audit is documented in
`.claude/skills/hifz-fsrs-invariants/SKILL.md`, invariant **H-6**. Read
that skill file before writing anything in `web/src/modules/scheduler/` --
it is the hard invariant spec for this area of the codebase.

The relevant rule, quoted from H-6:

> Every 30 days, a background job samples **5 random ayahs** from the
> mastered pool (`R ≥ 0.95 OR interval > 60d`). Sample size adjustable
> 3–10 based on pool size; default 5.

The skill's own worked example (lines 73-80) shows the pool-size-dependent
sample size as:

```ts
const sampleSize = Math.min(Math.max(masteredPool.length >= 50 ? 10 : 5, 3), 10);
```

Neither the eligibility filter nor this sample-size calculation exists
anywhere in the codebase as a pure, unit-testable function (confirmed:
nothing matches `masteredPool`, `oracleSample`, or `sampleSize` anywhere
under `web/src/`; `scripts/audits/fsrs_oracle_sweep.ts` referenced in the
skill's "Validation" section does not exist yet either -- this task is a
narrow, pure-logic slice of that future job, not the job itself: no
Supabase access, no actual random sampling, no I/O). This task is to write
the deterministic planning step that a future `fsrs_oracle_sweep.ts` would
call before doing any actual random draw.

## What to build

A new file `web/src/modules/scheduler/oracleAuditPlan.ts`, exporting:

```ts
export interface FsrsStateForAudit {
  ayahId: string;
  retrievability: number;
  intervalDays: number;
}

export function computeOracleAuditPlan(
  allStates: FsrsStateForAudit[],
): { eligibleAyahIds: string[]; sampleSize: number };
```

Exact spec:

1. **Eligibility** (the mastered pool): an entry of `allStates` is eligible
   if `retrievability >= 0.95` **OR** `intervalDays > 60` (OR, not AND --
   either condition alone qualifies; note the strict `>` on `intervalDays`
   but non-strict `>=` on `retrievability`, exactly as H-6 states it).
2. `eligibleAyahIds` is the `ayahId` of every eligible entry, in the same
   relative order they appeared in `allStates`. Do not deduplicate --if the
   input contains the same `ayahId` twice (eligible both times), it appears
   twice in the output.
3. **Sample size base**: per the skill's worked example,
   `eligibleAyahIds.length >= 50 ? 10 : 5` (the threshold is on the size of
   the **eligible pool**, i.e. `eligibleAyahIds.length` -- not
   `allStates.length`, which may include ineligible entries).
4. **Cap to what's actually available**: `sampleSize` must never exceed
   `eligibleAyahIds.length` (you cannot sample more ayahs than are
   eligible) -- `sampleSize = Math.min(<base from step 3>, eligibleAyahIds.length)`.
   This cap is a deliberate addition to the skill's worked example (which
   doesn't address a pool smaller than the base sample size); apply it
   exactly as stated here.
5. The function is pure: no I/O, no randomness (do not pick or return which
   ayahs are sampled -- only the plan: which ayahs are *eligible*, and how
   many to sample), no mutation of the input array, no `Date.now()`.

## Acceptance

Write acceptance tests in
`web/src/modules/scheduler/__tests__/oracleAuditPlan.test.ts` (matches this
repo's existing `__tests__` convention -- see the sibling files in that
directory for style). They must fail against the current code (the file
doesn't exist yet) and pass once correctly implemented. Cover at least: an
entry eligible by retrievability alone, an entry eligible by interval alone
(both >60 and the exact non-eligible boundary =60), an entry eligible by
both, an entry eligible by neither, the eligible-pool-size-50 boundary
(49 -> sampleSize 5, 50 -> sampleSize 10), a case where the eligible pool
is smaller than the computed base sample size (e.g. 2 eligible entries ->
sampleSize must be 2, not 5), duplicate `ayahId`s preserved in the output,
and no mutation of the input array.
