# Task: manzil catch-up flush distribution

## Context

hifz-companion's Friday manzil (whole-juz review) miss policy is documented
in `.claude/skills/hifz-fsrs-invariants/SKILL.md`, invariant **H-7**. Read
that skill file before writing anything in `web/src/modules/scheduler/` --
it is the hard invariant spec for this area of the codebase.

The relevant rule, quoted from H-7:

> **1st miss in rolling 28d window:** forgiven. Affected ayahs enter
> `catch-up_pool` with `+3d` interval extension. Pool flushes in chunks over
> the next 3 days to avoid queue collapse.

The function that decides WHICH ayahs go into which of those 3 days does not
exist yet anywhere in the codebase (confirmed: nothing matches `catch-up` or
`catchUp` anywhere under `web/src/`). This task is to write it.

## What to build

A new file `web/src/modules/scheduler/manzilCatchUp.ts`, exporting:

```ts
export function distributeCatchUpFlush(
  ayahIds: string[],
  missDate: Date,
): {
  day1: string[]; day2: string[]; day3: string[];
  day1Date: string; day2Date: string; day3Date: string;
};
```

Exact spec:

1. `day1Date`/`day2Date`/`day3Date` are `missDate` plus 1, 2, and 3 **calendar
   days** respectively, formatted `YYYY-MM-DD` in UTC (not 24h-multiples —
   add calendar days, so this is correct across a DST boundary or month-end).
2. Distribute `ayahIds` across the three days by **round-robin on input
   order**: the ayah at index `i` (0-indexed) of the input array goes to
   `day1` if `i % 3 === 0`, `day2` if `i % 3 === 1`, `day3` if `i % 3 === 2`.
   Within each day's array, preserve the relative order the ayahs appeared
   in the input.
3. Every ayah in the input appears in **exactly one** of the three output
   arrays — none dropped, none duplicated.
4. Empty input (`ayahIds = []`) returns all three day arrays empty, with the
   three dates still computed correctly from `missDate`.
5. The function is pure (no I/O, no mutation of the input array, no `Date.now()`).

## Acceptance

Write acceptance tests in `web/src/modules/scheduler/__tests__/manzilCatchUp.test.ts`
(matches this repo's existing `__tests__` convention — see the sibling files
in that directory for style). They must fail against the current code (the
file doesn't exist yet) and pass once correctly implemented.
