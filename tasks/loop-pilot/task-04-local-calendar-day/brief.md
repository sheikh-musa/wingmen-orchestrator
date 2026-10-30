# Task: user-timezone calendar-day comparison

## Context

hifz-companion's "no same-day double review" rule is documented in
`.claude/skills/hifz-fsrs-invariants/SKILL.md`, invariant **H-5**. Read
that skill file before writing anything in `web/src/modules/scheduler/` --
it is the hard invariant spec for this area of the codebase.

The relevant rule, quoted from H-5:

> Sleep consolidation (Rasch & Born 2013) requires >=1 sleep cycle between
> encoding and retrieval. The scheduler refuses to serve an ayah twice in
> the same local-day. **Implementer uses user's timezone, not UTC.**

`web/src/modules/scheduler/reconciler.ts` (around line 103-104) currently
computes `elapsedDays` as a raw millisecond difference
(`(eventAt.getTime() - lastReviewed.getTime()) / 86_400_000`), which is
**not** a timezone-aware calendar-day comparison -- it is only exactly `0`
when two timestamps are millisecond-identical, and never accounts for the
user's local timezone at all, contradicting H-5's explicit instruction.
There is no existing pure function anywhere in the codebase that answers
"are these two instants on the same calendar day, in a given IANA
timezone" (confirmed: nothing matches `isSameLocalDay`, `sameLocalDay`,
`localCalendarDay`, or `timezone` anywhere under `web/src/`, outside of
`__tests__` directories). This task is to write that missing primitive.
(Wiring it into `reconciler.ts`'s existing `elapsedDays` computation is
explicitly **out of scope** for this task -- write only the new pure
function and its tests; do not edit `reconciler.ts`.)

## What to build

A new file `web/src/modules/scheduler/localCalendarDay.ts`, exporting:

```ts
export function localCalendarDayKey(date: Date, timeZone: string): string;

export function isSameLocalDay(a: Date, b: Date, timeZone: string): boolean;
```

Exact spec:

1. `localCalendarDayKey(date, timeZone)` returns the calendar date `date`
   falls on **as observed in `timeZone`** (an IANA timezone identifier,
   e.g. `"America/New_York"`, `"Asia/Tokyo"`, `"UTC"`), formatted exactly
   `YYYY-MM-DD` (4-digit year, zero-padded month and day, in that field
   order). This must be correct across a DST transition in `timeZone` --
   i.e. it must resolve the actual UTC offset in effect at `date` for that
   specific timezone and date (not a fixed offset assumption), the same
   way `Intl.DateTimeFormat` with a `timeZone` option does.
2. `isSameLocalDay(a, b, timeZone)` returns `true` if and only if
   `localCalendarDayKey(a, timeZone) === localCalendarDayKey(b, timeZone)`
   -- i.e. it must actually use `timeZone` for both sides (never default
   to UTC or the host machine's local timezone internally), and must
   correctly return `true` for a pair of instants that fall on different
   **UTC** calendar days but the same **local** calendar day in
   `timeZone` (and vice versa: `false` for a pair on the same UTC day but
   different local days).
3. Both functions are pure: no I/O, no `Date.now()`, no mutation of the
   input `Date` objects.

## Acceptance

Write acceptance tests in
`web/src/modules/scheduler/__tests__/localCalendarDay.test.ts` (matches
this repo's existing `__tests__` convention -- see the sibling files in
that directory for style). They must fail against the current code (the
file doesn't exist yet) and pass once correctly implemented. Cover at
least: a UTC timestamp that is already "tomorrow" in a positive-offset
zone (e.g. `Asia/Tokyo`, no DST) and already "yesterday" in a
negative-offset zone (e.g. `America/New_York`); a pair of instants on
different UTC calendar days that land on the **same** local calendar day
in a given zone; a pair on the same UTC day that land on **different**
local days in a given zone; correctness across a real DST transition date
for `America/New_York` (verify against 2026's actual US DST rules, not an
assumption); and the exact `YYYY-MM-DD` output format of
`localCalendarDayKey` itself (not just `isSameLocalDay`'s boolean result).
