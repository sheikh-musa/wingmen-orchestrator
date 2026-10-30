# Task: Friday manzil miss classifier (rolling 28-day window)

## Context

hifz-companion's Friday manzil (whole-juz review) miss policy is documented
in `.claude/skills/hifz-fsrs-invariants/SKILL.md`, invariant **H-7**. Read
that skill file before writing anything in `web/src/modules/scheduler/` --
it is the hard invariant spec for this area of the codebase.

The relevant rule, quoted from H-7:

> - **1st miss in rolling 28d window:** forgiven. ...
> - **2nd miss in same 28d window:** protocol-review **notification** (not
>   auto-lapse). User chooses `catch-up | skip | reset`.
> - **3rd miss in same 28d window:** auto-flag `protocol_review` status;
>   next Friday treated as diagnostic (reduced scope, calibration intent).

The function that decides **which ordinal miss** a new Friday miss is
(1st/2nd/3rd+) within the rolling 28-day window, and therefore which of
those three statuses applies, does not exist anywhere in the codebase
(confirmed: `reconciler.ts:241` has a comment "full reconciliation against
manzil_miss_events is a follow-up" -- this task IS that follow-up, scoped to
pure classification logic only; it does not touch `manzil_miss_events`,
Supabase, or any I/O). This task is to write it.

## What to build

A new file `web/src/modules/scheduler/manzilMissClassifier.ts`, exporting:

```ts
export function classifyManzilMiss(
  pastMissDates: Date[],
  newMissDate: Date,
): {
  ordinalInWindow: number;
  status: "forgiven" | "protocol-review-notification" | "protocol-review-flagged";
  windowStartDate: string;
};
```

Exact spec:

1. The rolling window is the 28 **calendar days** ending on (and including)
   `newMissDate`'s calendar day -- i.e. `[newMissDate - 27 calendar days,
   newMissDate]` inclusive, computed in **UTC**. `windowStartDate` is that
   lower bound, formatted `YYYY-MM-DD` (UTC calendar-day arithmetic, not
   24h-multiples -- correct across a month/year boundary).
2. Compare every date by **UTC calendar day** (drop time-of-day; a
   `pastMissDates` entry on the same UTC calendar day as `newMissDate`, or
   on the same UTC calendar day as `windowStartDate`, is "in range" --
   both ends of the window are inclusive).
3. Count how many entries of `pastMissDates` fall strictly inside
   `[windowStartDate, newMissDate]` inclusive (by calendar day). Entries
   whose calendar day is **before** `windowStartDate` or **after**
   `newMissDate` are excluded -- `pastMissDates` may be unsorted and may
   contain dates outside the window on either side; do not assume it is
   pre-filtered or pre-sorted. A duplicate calendar day appearing twice in
   `pastMissDates` counts as **two** (each row is a real logged event, not
   a distinct-day set).
4. `ordinalInWindow` = (that count) **+ 1** (`newMissDate` itself is always
   counted, since it is the miss being classified right now -- a
   first-ever miss with an empty `pastMissDates` must return
   `ordinalInWindow: 1`, never `0`).
5. `status`: `ordinalInWindow === 1` -> `"forgiven"`;
   `ordinalInWindow === 2` -> `"protocol-review-notification"`;
   `ordinalInWindow >= 3` -> `"protocol-review-flagged"`.
6. The function is pure: no I/O, no mutation of the input `pastMissDates`
   array, no `Date.now()`.

## Acceptance

Write acceptance tests in
`web/src/modules/scheduler/__tests__/manzilMissClassifier.test.ts` (matches
this repo's existing `__tests__` convention -- see the sibling files in
that directory for style). They must fail against the current code (the
file doesn't exist yet) and pass once correctly implemented. Cover at
least: first-ever miss (empty input), a 2nd miss inside the window, a 3rd+
miss inside the window, a past miss exactly on the window's lower boundary
(27 days before, inclusive -- must count), a past miss one day outside the
window (28 days before -- must NOT count), a past-dated entry that is
actually after `newMissDate` (must NOT count), a window crossing a
month/year boundary, and no-mutation of the input array.
