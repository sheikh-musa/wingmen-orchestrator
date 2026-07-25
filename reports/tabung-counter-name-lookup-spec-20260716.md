# Spec: Student-name lookup in Tabung Keluarga Counter (op#4635 #6 / op#4640 Track A)

**Owner:** cc-ihsanos lane (build authored-unapplied) → hub review+gate → deploy → Nazim verify → relay.
**Type:** Track A, non-destructive, read-only search. NOT money/residency-gated (in-tenant read), BUT has a real **PII-in-bulk** concern — guards below are mandatory, not optional.

## Goal
A teacher/counter operator often knows the student but not the tin serial. Today the Counter "Look Up" resolves a tin by **serial only**. Add lookup by **student name** so they can find the student's tin(s) without the serial.

## Current state (verified)
- Route: `src/app/dashboard/tabung/keluarga/counter/page.tsx` → client `counter-scan-client.tsx`.
- Serial lookup: `lookupTinWithStudentAction(barcode)` in `src/actions/tabung-keluarga.ts:775-838` — exact-match single row on `tabung_kk_tins.serial_number`, embeds `sch_students!inner → persons!inner(display_name, phone_encrypted) → sch_classes(name)`, decrypts parent phone via `decryptPii`.
- Data model: `tabung_kk_tins.student_id → sch_students.id`; `sch_students.person_id → persons.id` (holds `display_name`); `sch_students.class_id → sch_classes.id`. Name lives on `persons.display_name`.
- **Reuse pattern (mirror this):** the name-search block inside `listUnreturnedKkTinsAction` (`tabung-keluarga.ts:1032-1066`) already resolves `persons.display_name ilike` → `sch_students` → tins, with the PostgREST "resolve ids top-level first" workaround. Copy that shape.

## Build

### 1. New server action `lookupTinsByStudentNameAction(name: string)` (in `tabung-keluarga.ts`)
- `getOrgContext()` guard (bail if `!orgId`); Zod-validate input.
- Resolve `persons.id` by `.eq("org_id", orgId).ilike("display_name", "%name%").limit(N)` → `sch_students.id` (`.eq org_id`, `.in person_id`, `.is deleted_at null`, `.limit(N)`) → select `tabung_kk_tins` `.eq("org_id", orgId).in("student_id", studentIds).is("deleted_at", null)`.
- Returns an **array** of `{ tin, student:{id,student_number,class_name} }` (a name matches many students/tins). Same `ActionResult<T[]>` shape.
- `captureActionError(err, { action: "lookupTinsByStudentNameAction" })` in catch (gate `check-action-error-capture`).

### 2. UI: second input in `counter-scan-client.tsx`
- Add a "Look up by student name" input beside the serial input (serial stays the primary path).
- Debounced (mirror `donations/donor-search.tsx` `debounceRef`+setTimeout), fires the new action, renders a compact result list (name · class · serial). Selecting a row funnels into the **existing** `runLookup(serial)` / result-card render so the confirm/mark-banked flow is unchanged.

## PII guards — MANDATORY (this is the review gate's first check)
1. **No bulk PII decrypt.** The name-search select must **NOT** include `phone_encrypted` / call `decryptPii`. Return only `display_name`, `student_number`, `class_name`, serial. Parent phone is decrypted **only** on single-tin select via the existing serial path. (Convention: `core/persons/api.ts:13` "never receive PII in bulk"; `searchPersons` excludes NRIC.)
2. **Minimum query length ≥ 3 chars** — reject/no-op shorter queries so "a" can't enumerate the roster.
3. **Hard result cap** (e.g. `.limit(25)` on the final tin select) + surface "refine your search" when capped.
4. **Tenant scoping on ALL three tables** (`persons`, `sch_students`, `tabung_kk_tins`) — explicit `.eq("org_id", orgId)`; RLS is backstop only.

## Conventions / lint gates
- `check-pagination`: every `.select()` needs `.limit()/.single()/.maybeSingle()` within 8 lines.
- `check-schema-drift`: use id-only selects (`.select("id")`) on resolvers to avoid drift; else add ignore comment.
- `check-module-boundaries`: keep it in `src/actions/tabung-keluarga.ts` (persons/sch_*/tabung_kk_* are in-bounds).
- `no-explicit-any` is a hard error — use the existing `as unknown as Joined` cast style.

## Tests + verification (required before "done")
- Unit/vitest for the action: name matches → returns tins; short query (<3) → rejected/empty; result cap enforced; **assert the select does NOT decrypt phone in bulk**; tenant scoping present.
- Component test: name input debounces + renders list + selecting a row drives the existing lookup.
- Hub eyeball: render the counter with the new input (mobile+desktop) before deploy.
- CI: unit-tests + tabung-correctness green (display/read-only, no figure change). `lint-and-typecheck` red is pre-existing — verify 0 new tsc errors in touched files.

## Index note (flag, not a blocker)
`idx_persons_org_name(org_id, display_name)` is btree — won't accelerate `%substring%`. Per-org row counts are small so a seq-scan is acceptable; if `check-index-coverage` flags it, either prefix-anchor (`name%`) or note a future `pg_trgm` GIN index to whoever owns migrations. Do NOT add a migration in this PR without hub sign-off.

## Out of scope
No schema/migration changes. No changes to the serial path, confirm, or mark-banked flows beyond wiring the name-select into them.
