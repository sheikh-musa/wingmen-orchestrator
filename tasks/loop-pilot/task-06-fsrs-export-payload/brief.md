# Task: FSRS export payload (full state + audit log, as a stable snapshot)

## Context

hifz-companion's "no lock-in" rule is documented in
`.claude/skills/hifz-fsrs-invariants/SKILL.md`, invariant **H-9**. Read
that skill file before writing anything in `web/src/modules/backup/` -- it
is the hard invariant spec for this area of the codebase.

The relevant rule, quoted from H-9:

> User must be able to export full FSRS state + session history + audit
> log as a single JSON blob at any time... Exported format is documented
> and stable.

`web/src/modules/backup/service.ts` already has a working
`createBackupPayload()` for `progress`/`sessions`/`downloads`/`settings` --
**that function is correct, already shipped, and out of scope; do not
edit it.** It does not cover FSRS scheduler state or the `fsrs_audit_events`
audit log H-9 explicitly calls out, and no such function exists anywhere
else either (confirmed: nothing matches `fsrsExport`, `auditLog`, or
`FsrsExportPayload` anywhere under `web/src/`). This task is to write that
missing FSRS-specific slice, as a sibling module.

Note one deliberate departure from `createBackupPayload`'s own convention:
that function calls `Date.now()` internally, which is fine for its
production call sites but makes it impossible to unit-test the exact
`exportedAt` value deterministically. This task's function takes `now` as
an explicit parameter instead -- follow the spec below exactly, not
`createBackupPayload`'s internal-`Date.now()` pattern.

## What to build

A new file `web/src/modules/backup/fsrsExportPayload.ts`, exporting:

```ts
export interface FsrsExportPayload {
  schemaVersion: "1";
  exportedAt: string; // ISO 8601
  rowCounts: { state: number; events: number; auditLog: number };
  state: unknown[];
  events: unknown[];
  auditLog: unknown[];
}

export function createFsrsExportPayload(
  state: unknown[],
  events: unknown[],
  auditLog: unknown[],
  now: Date,
): FsrsExportPayload;
```

Exact spec:

1. `schemaVersion` is always the literal string `"1"`.
2. `exportedAt` is `now.toISOString()` -- the function must never call
   `Date.now()` or construct `new Date()` internally; it only ever uses
   the `now` it was given.
3. `rowCounts` is `{ state: state.length, events: events.length, auditLog: auditLog.length }`,
   computed from the **original** input arrays' lengths.
4. The `state`/`events`/`auditLog` fields on the returned payload are
   **independent snapshots**, not references to the caller's arrays: every
   row and its contents must be preserved exactly, in the same relative
   order, with none dropped, none duplicated, and none deduplicated (if
   the caller happens to pass the same row object twice, it appears twice
   in the output) -- but mutating the caller's original arrays or any of
   their row objects **after** calling this function must never change
   the already-returned payload. This is a deep-enough copy to guarantee
   that property for arrays of plain JSON-shaped objects (you do not need
   to handle functions, class instances, or circular references).
5. The function is pure otherwise too: it must never mutate the original
   `state`/`events`/`auditLog` arrays or their row objects while building
   the payload (e.g. no in-place `.sort()` on the input).

## Acceptance

Write acceptance tests in
`web/src/modules/backup/__tests__/fsrsExportPayload.test.ts` (this repo
doesn't have a `__tests__` directory under `backup/` yet -- create it;
match the style of the sibling `__tests__` directories elsewhere in
`web/src/modules`). They must fail against the current code (the file
doesn't exist yet) and pass once correctly implemented. Cover at least:
`schemaVersion` is exactly `"1"`, `exportedAt` matches `now.toISOString()`
for an explicitly-passed `now` (not the real wall clock), `rowCounts`
matches each input array's length, all rows preserved in order across all
three arrays, a duplicate row object appearing twice in the input appears
twice in the output (not deduplicated), and -- the sharpest test --
mutating an object in the ORIGINAL `state` array after calling
`createFsrsExportPayload` does **not** change the corresponding object in
the already-returned payload.
