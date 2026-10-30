# Task: ayah status classifier (hifz fluency + tajweed, never collapsed)

## Context

hifz-companion tracks memorization recall and tajweed (pronunciation)
correctness as two separate dimensions, per invariant **H-8** in
`.claude/skills/hifz-fsrs-invariants/SKILL.md`. Read that skill file
before writing anything in `web/src/modules/scheduler/` -- it is the hard
invariant spec for this area of the codebase.

The relevant rule, quoted from H-8:

> FSRS D/S/R state covers recall accuracy of the ayah text. Tajweed
> correctness is a sibling dimension with its own state machine, separate
> table (`hifz_tajweed_state`), separate grades. An ayah can be "fluent in
> hifz" and "weak in tajweed" simultaneously -- **collapsing them corrupts
> both signals.**

There is no existing pure function anywhere in the codebase that computes
a UI-facing status classification from both dimensions while keeping them
independently inspectable (confirmed: nothing matches `classifyAyah`,
`ayahStatus`, or `tajweedWeak` anywhere under `web/src/`, outside of
`__tests__` directories; `web/src/modules/mushaf/tajweed.ts` is an
unrelated module -- pure text-shaping/rendering helpers for displaying
tajweed coloring rules in the Mushaf view, not memorization-vs-tajweed
grading). This task is to write that missing classifier.

## What to build

A new file `web/src/modules/scheduler/ayahStatusClassifier.ts`, exporting:

```ts
export interface AyahMemorizationStatus {
  retrievability: number; // FSRS-5 R, 0..1
  isLeech: boolean;
}

export interface AyahTajweedStatus {
  lastGrade: 1 | 2 | 3 | 4; // same 1-4 grade scale as FSRS grades
}

export function classifyAyahStatus(
  memorization: AyahMemorizationStatus,
  tajweed: AyahTajweedStatus,
): {
  hifzFluent: boolean;
  tajweedWeak: boolean;
  combinedLabel:
    | "fluent-and-correct"
    | "fluent-tajweed-weak"
    | "weak-hifz-tajweed-ok"
    | "weak-both";
};
```

Exact spec:

1. `hifzFluent` is computed **only** from `memorization`:
   `retrievability >= 0.9 AND NOT isLeech`. It must never be influenced by
   `tajweed.lastGrade` in any way -- this is the H-8 invariant: the two
   dimensions never cross-contaminate.
2. `tajweedWeak` is computed **only** from `tajweed`:
   `lastGrade <= 2` (grades 1-2 = weak/"again"/"hard"; grades 3-4 =
   acceptable/"good"/"easy", the same 1-4 scale FSRS grades already use
   elsewhere in this codebase). It must never be influenced by
   `memorization` in any way.
3. `combinedLabel` is a pure, deterministic function of the two
   **already-computed** booleans above (not re-derived from the raw
   inputs):
   - `hifzFluent=true, tajweedWeak=false` -> `"fluent-and-correct"`
   - `hifzFluent=true, tajweedWeak=true` -> `"fluent-tajweed-weak"`
   - `hifzFluent=false, tajweedWeak=false` -> `"weak-hifz-tajweed-ok"`
   - `hifzFluent=false, tajweedWeak=true` -> `"weak-both"`
4. The function is pure: no I/O, no mutation of the input objects, no
   `Date.now()`.

## Acceptance

Write acceptance tests in
`web/src/modules/scheduler/__tests__/ayahStatusClassifier.test.ts`
(matches this repo's existing `__tests__` convention -- see the sibling
files in that directory for style). They must fail against the current
code (the file doesn't exist yet) and pass once correctly implemented.
Cover at least: all 4 `combinedLabel` combinations, the exact
`retrievability` boundary (`0.9` itself counts as fluent, `0.899...`
does not), a fluent-by-retrievability ayah that is still a leech (not
fluent -- `isLeech` must override), and the exact `lastGrade` boundary
(`2` is weak, `3` is not) -- in particular a case proving `hifzFluent` is
unaffected by a low `lastGrade` and `tajweedWeak` is unaffected by a low
`retrievability` (the H-8 no-cross-contamination property).
