# COSEM Unified Port — Migration Architecture Design

**cosem-adcda + cosem-tdu → cosem-platform** · Fable-5 Migration Architect · 2026-07-22
Frontend layer: `~/wingmen/projects/cosem-platform` (Next.js 16, one shared codebase, all tenants).
Data layer: Supabase Postgres, project ref **not yet registered** (`REPOS.json` and
`docs/data-store-registry.md` both show `supabase_project_ref: null` for cosem-platform today —
see §3.6, this is the first concrete action item, not a detail to gloss over per LAYER-VOCAB-001).

**Scope & method.** This design is grounded in the current committed state of all three repos:
26 applied migrations / 39 tables in `supabase/migrations/`, `src/shared/lib/{modules,capabilities}.ts`,
`AGENTS.md`, `DEMO.md`, `docs/onboarding-module-spec.md`, and the code (not just comments) in
`src/modules/{nfpa/eligibility.ts, assets/nea-access.ts, people/queries.ts}` — read directly, cited
below by file/line where it changes a recommendation. No data file was opened in any repo (no
`.xlsx`/`.docx`/namelists/results) — everything below is CODE, SCHEMA, and FLOW analysis, per the
standing guardrail. Every schema/table this document proposes is for **synthetic data only** until
its own residency gate (§3) clears.

---

## 0. Ground truth snapshot (verified, not inherited from the brief)

- **39 tables, 26 migrations**, `20260709142346_foundation.sql` → `20260721130000_...`. Full
  `create table` inventory confirmed by grep — no table exists that isn't listed in §1.3/§2.
- **`SCAFFOLDED_MODULES` is empty** (`src/shared/lib/modules.ts:32`) — every module except `lms`
  has already graduated to `LIVE_FEATURE_MODULES`. "Scaffolded" in the cosem-tdu op #4251 sense means
  *permission plumbing exists*, not *UI exists* — don't read it as "half-built."
- **`org_members.role` and `org_role_permissions.role` are free text — no DB CHECK/enum**
  (`20260713160000_role_rename.sql:10`, confirmed directly: "this is a pure data migration" precisely
  *because* there's nothing to alter). Role safety today is TypeScript-only. See risk R4.
- **`staff` has no `user_id` column** (`20260714120000_functional_roles_and_staff.sql:110-129`, read
  in full) — unlike `trainees.user_id`, which `20260720120000_trainee_login_accounts.sql` added on
  purpose. See risk R2 — this is the single highest-value hardening item before any real staff
  self-attendance cutover.
- **`attendance_events` is staff-only** (`staff_id uuid not null references staff(id)`) — there is
  **no trainee/course/batch attendance table**. ADCDA's original attendance surface was primarily
  about *trainees* (namelist-driven roster marking), only secondarily about trainer self-check-in.
  This is a real, unbuilt gap, not a "just flip it on." See §1.3 and risk R3.
- **DEMO.md is already stale against shipped code** — DEMO.md:72 says the full skill-sheet
  transactional workflow (retest caps, optimistic locking, signatures) is "deferred post-demo," but
  `src/modules/nfpa/eligibility.ts` (attempt_no, student+verifier signatures, Asia/Dubai same-day-retest
  rule) plus the `20260713140000_nfpa_practical.sql` / `20260720140000_practical_sets.sql` /
  `20260721130000_...` migrations — all dated *after* whatever pass wrote that DEMO.md line — show it
  shipped. This is the exact "docs drift from code" failure mode the port is supposed to retire, now
  caught recurring inside cosem-platform itself. See risk R5.
- **Two parallel "skill sheet" tracks coexist**: the exams module's generic `skill_sheets` /
  `skill_sheet_steps` / `skill_sheet_matrix` (`20260709151740_skill_sheets.sql`) vs. the NFPA module's
  `nfpa_skill_plans` / `skill_assessments` (`20260713140000_nfpa_practical.sql`). They are not the same
  thing and must not be wired interchangeably. See §1.3 and risk R6.
- **The NEA external-access seam is deliberately unwired** (`src/modules/assets/nea-access.ts`,
  `NEA_EXTERNAL_ACCESS_ENABLED = false`) with a fully documented three-layer design and explicit
  "DECISION REQUIRED (operator + cai)" — this is the house style to *extend*, not a half-finished
  feature to complete opportunistically. See §3.5 and risk R7.
- **The biometric stub pattern is already proven**: `attendance_events` carries
  `biometric_status`/`liveness_passed`/`match_distance` columns that are populated `NULL`/`'capture_pending'`
  and explicitly documented as unused pending "a separate PDPA + TENANT-RESIDENCY-001 grant"
  (`20260715120000_attendance_module.sql:6-11`). This is the template §3 generalizes to every other
  gated field (face descriptors, full Emirates ID, geofence enforcement).
- **`organizations.type check (type in ('academy','unit','company'))` and `.region text`** already
  fit both tenants with zero schema change: ADCDA → `type='academy', region='ae'`; TDU →
  `type='company', region='sg'`.
- **ADCDA is pre-live** — "the app itself is pre-live (awaiting the first real trainee batch)." This
  single fact changes the entire Track A migration calculus in §4: there is no live-cutover problem
  for ADCDA's trainee data because there is no real trainee data yet.
- **TDU is live with real Singapore staff PII** and an in-flight, not-yet-shipped wages feature
  (`docs/superpowers/specs/2026-06-22-wages-view-design.md` in cosem-tdu) — and cosem-platform
  *already has a capability-level home waiting for it* (`finance` functional role,
  `hr.wages.view/manage`, `reports.wages.view` in `capabilities.ts:32-33,44,68`), unused today. This
  is a genuine leapfrog opportunity, not just a port target — see §2 and §4.2 Phase 5.

---

## 1. Unified multi-tenant data model

### 1.1 Tenant isolation strategy — two levels, not one

The codebase already has a correct, working mechanism for one level of isolation and an *explicit,
self-declared* doctrine for the other that just hasn't been executed yet. Don't invent a new
mechanism — compose the two that already exist:

**Level 1 — ORG (`org_id` + RLS).** Fully live today (`organizations`/`org_members`/RLS/
`auth_user_org_ids()`/`auth_user_has_capability()`, `AGENTS.md:33-44`). This is the *only* isolation
mechanism needed between tenants that share a jurisdiction and don't have a dedicated-silo grant —
the exact shape `docs/data-store-registry.md` already sanctions for ihsanos ("ihsanos multi-tenant
DB... default home for tenants w/o a silo"). **TDU is this case** (see §3.4).

**Level 2 — JURISDICTION (separate Supabase project + separate deployment).** `AGENTS.md:43-44`
states this in the codebase's own words: *"Jurisdiction siloing: one codebase, a separate Supabase
project per jurisdiction; identical schema, only the connection target differs. No cross-jurisdiction
row shares a database."* This is not yet executed for cosem-platform (only one project target exists,
ap-southeast-1/SG, ref unregistered) but the doctrine is already correct and matches
TENANT-RESIDENCY-001 exactly. **ADCDA is this case** (UAE gov-PII, different jurisdiction from the
SG project) — mandatory, non-negotiable, not a config toggle. See §3.3.

Recommendation: mirror the **ihsanos / ihsanos-irsyad split** precedent exactly (already fleet
doctrine, already proven) — one Vercel deployment + one Supabase project per jurisdiction, identical
git repo and migrations replayed into each. `src/proxy.ts`'s existing host-label → `x-org-slug`
routing stays a Level-1 (multi-org, single-DB, single-jurisdiction) mechanism; it should **not** grow
into a per-request datasource switch. Picking a Postgres connection dynamically per incoming request
based on the resolved org's `region` is a residency bug waiting to happen (one misrouted request =
one cross-jurisdiction row); a deployment-level split makes the wrong-database class of bug
structurally impossible instead of runtime-conditional.

```
                    ┌─ cosem-platform-sg (Vercel) ── ihsanos-pattern SG Supabase project ──┐
frontend (1 repo) ──┤     orgs: Meridian(demo), TDU(real, org-scoped), future SG academies │
                    └─ cosem-platform-ae (Vercel) ── NEW UAE-silo Supabase project ─────────┤
                          orgs: ADCDA(real, org-scoped from birth)
```

### 1.2 Shared vs. per-tenant entities

| Layer (LAYER-VOCAB-001) | What | Shared or per-tenant |
|---|---|---|
| **frontend** | Next.js app, `src/modules/*`, `src/shared/lib/{modules,capabilities}.ts`, migrations DDL | **Shared** — one repo, one deploy artifact per jurisdiction, identical code |
| **data** | Every row in every tenant table | **Per-org** via `org_id`, within a jurisdiction's project |
| **data** | Which project a jurisdiction's rows live in | **Per-jurisdiction** — a deployment-level decision, not a row |
| **data** | `organizations.settings.modules`, `org_role_permissions` | **Per-org config** — same schema, tenant-tunable values (already live, no-rebuild) |
| **data** | `org_user_functional_roles`, `staff`, `trainees` | **Per-org rows** — the actual client data |

### 1.3 Entity reconciliation — Firestore collection → Postgres table

Both legacy apps are Firestore/NoSQL with no migration history (`docs/DATA_MODEL.md` in both was
found stale against code); this table is the re-derivation, cross-checked against the live DDL.

| adcda collection | tdu collection | → cosem-platform | Status |
|---|---|---|---|
| `trainees` | `staff` (their trainee-equiv., confusingly named — see risk R8) | `trainees` | **LIVE** (`20260710120000_onboarding_module.sql`) + `user_id`/`dob`/`emirates_id_last4` (`trainee_login_accounts.sql`) — the PIN scheme was built with exactly ADCDA's Emirates-ID shape in mind. **Needs**: an `attributes jsonb not null default '{}'` bag (mirroring `assets.attributes`, `20260715130000_assets_module.sql:65`) for PPE-issuance booleans, the "Arif" flag, uniform/cert details that don't earn first-class columns |
| `trainers` | `regulars` (their trainer-equiv.) | **splits three ways**: `org_members` (login + base role) + `org_user_functional_roles` (trainer/tc/regulator capability) + `staff` (name/photo/employment metadata) | This is a genuine re-normalization, not a 1:1 port — one Firestore doc becomes three joined rows. See §1.4 |
| `observations`, `trainingPhotos` | `observations` | *(none)* | **Not built.** No observations-shaped table exists in any of the 39. Build item, see §2/§4 |
| `incidentReports` | `incidentReports` | *(none)* | **Not built.** Bilingual EN/AR — platform's mechanically-enforced i18n (`scripts/lint/check-i18n.mjs`) makes this *easier* to build correctly than either legacy app's partial coverage |
| `inventory` (pumper/vehicle) | `inventory`, `inventoryChecks`, `defects`, `maintenanceChecklists`/`maintenanceLogs`, `chargingRegimeLogs`, `testRunLogs`, `chargingSchedules` | `stores`→`assets`→`asset_service_events`+`asset_defect_reports`+`asset_charging_logs` | **LIVE** (`20260715130000_assets_module.sql`). **Gap**: no table for a *forward-looking recurring* test-run/charging schedule (`testRunLogs`/`chargingSchedules` as a *plan*, not a completed event) — confirmed by full grep, only event-log tables exist. Build `asset_service_schedules`, shape mirrors `assessment_schedule` |
| `attendanceSessions` (group roster) + `trainerAttendanceEvents` (self, biometric+geofence) | `attendanceSessions` + `staffAttendanceEvents` | `attendance_events`+`attendance_day_closures` (staff self-mark only) | **LIVE for staff self-attendance** (`20260715120000_attendance_module.sql`), non-biometric — matches both apps' current reality (liveness disabled in both). **NOT covering trainee/batch roster attendance** — `attendance_events.staff_id` FKs to `staff`, not `trainees`. Do not force trainees into `staff` (the migration's own comment forbids conflating them, line 106-108). Build a separate trainee/course attendance concept — see risk R3 |
| `scdf_theory_results` | `tdsct_theory_results` (rules still say `scdf_*`, doc drift, low-risk — Admin-SDK-only) | `theory_exams`/`theory_attempts` (generic MCQ) for ADCDA-shaped, OR `tdsct_theory_attempts` (reuses exams grading engine) for TDU-shaped | **LIVE**, both tracks. Pick per-tenant based on programme shape, not blanket |
| `scdf_practical_results` | `tdsct_practical_results` | `practical_rubrics`/`practical_assessments` (generic binary checklist) for ADCDA's SCDF-practical, `tdsct_practical_assessments` (station-rubric) for TDU's multi-station TDSCT | **LIVE**, both tracks |
| `skillSheets`/`skillSheetResults` | *(TDU has no NFPA-standard skill-sheet track)* | `nfpa_skill_plans`/`skill_assessments` (+ eligibility via `src/modules/nfpa/eligibility.ts`) | **LIVE, attributed port** — see §1.3 note below. **NOT** the generic exams `skill_sheets` table (different, older, non-eligibility-checked track — risk R6) |
| `trainees/{id}/notes` + `traineeAuditLog` | *(similar)* | *(none — see below)* | `audit_log` (LIVE, hash-chained) covers system mutation history. **Notes** (transfer/disciplinary/injury, immutable once written, per-trainee) need a dedicated `trainee_notes` table — a case-note is a business record with immutability as a *rule*, not just an audit artifact; don't overload `audit_log` or bury it in `trainees.attributes` jsonb where an RLS policy can't enforce append-only |
| `phoneAllowlist`, `passkeyChallenges`/`passkeyIndex`, WebAuthn, face descriptors | same shape | *(none — deliberately)* | **Explicitly out of scope**, matching `docs/onboarding-module-spec.md:20-22`'s own stated v1 cut. Not a gap, a decision already made — keep it that way until a real need forces it |
| `config/course_info` geofences | `config/course_info` + `jobGeofencePolicy.js` | *(none)* | **Not built** — consistent with biometrics being deferred; `attendance_events.geo` is captured but "no policy enforcement yet" (migration comment). When built: `attendance_zones(org_id, name, center, radius_m, applies_to jsonb)`, modeled on `jobGeofencePolicy.js` |
| `generatedReports` (Docs/Slides/Drive templating, hardcoded resource IDs) | same | *(none — and should stay none)* | **Do NOT port this mechanism.** It's single-tenant by construction (one hardcoded Master Spreadsheet/template-ID set per `functions/index.js`) — the opposite of what a shared platform needs. Platform's own reporting doctrine (`xlsx` export, `buildMonthlyReport()`-style pure builders) is the right replacement |
| Three browser-print PDF templates (BAPT/ORBAT/Attendance) | *(n/a, ADCDA-specific)* | *(none yet, but this IS the port target)* | **Port as-is** — self-contained React components + two data calls (trainees, course info); the prompt's own portNotes call this the easiest port in the whole codebase, confirmed true by design: swap the data-fetching hook for `@modules/onboarding` queries, done |
| `pipelines/adcda-bot` (Telegram→OCR→grade→Sheets, BAPT scoring) | *(n/a)* | *(none — low priority build)* | The pure rule engine (`lib/grade.js` — lowest-of-5-lettered-components, 2nd-reading>50, never-guess) is portable nearly verbatim into a new pure function (nfpa or a `bapt` sub-module). The Telegram/Drive/Sheets ingestion shell stays a standalone worker; point its upsert step at a new gated server action (`importBaptScores`) that **re-derives the grade server-side from the same pure function** rather than trusting the worker's number — consistent with the platform's "server-side grading only" doctrine already enforced for exams |
| `notificationDispatchLogs`, FCM push | same | *(none)* | Bundle with whichever phase ships `observations` (its main trigger in both legacy apps) |

**Note on the NFPA attribution** (`src/modules/nfpa/eligibility.ts:4-12`, read in full): the port
already demonstrates the correct method — same check order and the Asia/Dubai same-day-retest rule
copied faithfully, but the hard attempt-cap (ADCDA defaulted to 3) was made **opt-in**
(`DEFAULT_MAX_ATTEMPTS = 0`) specifically because it would otherwise contradict the platform's own
grading doctrine ("re-sits are grade-capped at 60%, not blocked"). **This is the template for every
other TDU/ADCDA behavior being ported: reconcile against platform doctrine, don't transplant
blindly.** Apply the same discipline to TDU's attendance/wages/inventory logic in §4.

### 1.4 Role reconciliation

Platform's current shape: 4 base roles (`admin`/`assessor`/`student`/`verifier`,
`modules.ts:40`) × org-tunable module matrix, **union** 6 functional roles
(`trainer`/`tc`/`regulator`/`hr`/`ops`/`finance`, `capabilities.ts:16`) that *add* discrete
capabilities, resolved at one gate (`resolveEffectiveAccess`). This is strictly better-structured
than either legacy app's flat role list, but it has one real hole for this port: **every functional
grant is additive** — there is no mechanism to hand someone a *narrower-than-default* slice of a
base role's matrix that differs from everyone else sharing that base role at the same org. ADCDA's
`local_instructor` (hard-scoped to onboarding/namelist only) and `skill_sheets_editor` (NFPA
skill-sheet authoring only) are exactly this shape — and ADCDA's own answer to that gap
(per-user `extraPermissions`, arbitrary `module:action` grants) is the mechanism that produced its
documented SEV-1 role-self-escalation bug. **Do not re-import that mechanism.**

Recommended fix — one small, generalizable addition, following the *exact* precedent already in
`capabilities.ts:47-54` (the `tdsct.assess`/`tdsct.verify`/`exams.author` comment literally documents
"discrete authority finer than module full/read... decoupled from module access ON PURPOSE"):

1. **Add a 5th base role, `member`**, with an **empty** `DEFAULT_ROLE_PERMISSIONS` entry (everything
   `'none'`) — a deliberate "no default access" floor. Someone with base role `member` gets 100% of
   their access from functional/capability grants, never from an opinionated bundle. Cheap
   (TS `Role` union +1, `ROLES` array +1, one `DEFAULT_ROLE_PERMISSIONS` entry, a `role_rename.sql`-shaped
   seeding migration) and reusable by any future tenant's narrow-scope worker, not an ADCDA/TDU hack.
2. **Add two discrete capabilities** to `CAPABILITY_REGISTRY`: `onboarding.manage` (module=`onboarding`,
   access=`full`) and `exams.skillsheets.author` (module=`exams`, access=`full`, splitting the current
   blanket `exams.author`). Grant each via a small functional-role addition (or extend an existing one)
   — same shape as every other capability already in the table.
3. **Every real `extraPermissions` grant observed in the legacy apps maps to one of the recipes below.**
   If a genuinely novel one-off surfaces later, the answer is "propose a new named capability" (a
   migration + code review), never a live per-user toggle.

| Legacy role | Source | → base role | + functional/capability grants | + linked rows |
|---|---|---|---|---|
| `trainee` | adcda | `student` | — | `trainees` row, `user_id`-linked |
| `user` | tdu | `student` | — | `trainees` row, `user_id`-linked |
| `trainer` | adcda | `assessor` | `trainer` (+`tc` if calendar-managing) | `staff` row via new `staff.user_id` (§1.3, risk R2), for name/photo/employment metadata |
| `regular` | tdu | `assessor` | `trainer` (+`tc`) | `staff` row (same as above) |
| `local_instructor` | adcda | **`member`** (new) | `onboarding.manage` (new capability) | — |
| `skill_sheets_editor` | adcda | `member` or `assessor` (tenant's choice) | `exams.skillsheets.author` (new capability) | — |
| `admin` | both | `admin` | — | — |
| `super_admin` | both | `admin` (folds — see note) | — | — |
| `part_timer` | tdu | **`member`** (new) | `ops` (inventory) + `trainer` (self-attendance view/mark only) | `staff` row |
| — (finance) | tdu (in-flight, unshipped) | any | `finance` (**already exists**, unused: `hr.wages.*`/`reports.wages.*`) | — |

**Admin/super_admin fold note**: collapsing to one `admin` matches the *already-executed* precedent
in `role_rename.sql` (`org_admin`/`instructor`/`designer` → `admin`) and is the correct **default**.
But both legacy apps built the split specifically so a lower admin tier couldn't touch the
permission matrix or grant elevation — for a real UAE government client (ADCDA) this two-tier
signoff need is plausible enough that it should be **explicitly confirmed with the tenant before
real go-live**, not silently assumed away. If needed, it's the same `member`-role mechanism run in
reverse is *not* the right tool (that adds narrowness, not a second admin tier) — a genuine second
admin tier would be a 6th base role. Flag to cai/operator if ADCDA asks for it; don't build it
speculatively.

### 1.5 New schema this design calls for (summary — detail in §4)

`trainees.attributes jsonb`, `staff.user_id uuid references auth.users`, `trainee_notes`,
`observations` (+worklog/assignment), `incident_reports`, a trainee/course attendance table
(name TBD — not `attendance_events`, see R3), `asset_service_schedules`, `attendance_zones`
(deferred until geofence policy is prioritized), plus the `member` base role + two capabilities
from §1.4. None of these are exotic — every one follows the `org_id` + RLS +
`auth_user_has_capability()` + soft-delete shape already uniform across all 39 existing tables.

---

## 2. Feature-mapping table

Legend: **LIVE** = shared module already ported and ready · **CONFIG** = zero code, an org-level or
role-matrix setting · **BUILD-NEW** = no platform equivalent exists yet · **BUILD-EXT** = existing
module needs new columns/tables alongside it · **PORT-LOGIC** = a specific file/function transplants
near-verbatim · **DON'T-PORT** = legacy pattern to replace, not carry over · **GATED** = seam may
exist but stays off behind its own PDPA/TENANT-RESIDENCY-001 grant · **DECIDE** = a real fork,
escalate rather than resolve silently.

| Feature | Source | Platform destination | Treatment |
|---|---|---|---|
| Trainee/user capture (photo, ID, batch/group) | both | `src/modules/onboarding` | **LIVE** |
| Namelist roster CRUD, dedup, archive | adcda | `src/modules/onboarding` | **LIVE** |
| Bulk `.xlsx` namelist import | adcda | `bulkImportTrainees` | **LIVE** |
| Emirates-ID OCR autofill (server Vision API) | adcda | client-side `tesseract.js` prefill (already in onboarding) | **LIVE**, different mechanism — functional equivalent, not a parity claim |
| 3 bilingual pixel-faithful printables (BAPT/ORBAT/Attendance) | adcda | new `/print/*` routes | **PORT-LOGIC** — React components transplant near-verbatim per §1.3 |
| Google Docs/Slides/Drive-templated report generation | both | — | **DON'T-PORT** — single-tenant hardcoded resource IDs; replace with `xlsx`/pure-builder export pattern already used elsewhere |
| Trainer/staff self-attendance (non-biometric) | both | `src/modules/attendance` | **LIVE**, but see R2 (identity-bind gap) before real cutover |
| Trainee/course roster (batch) attendance | adcda primarily | — | **BUILD-NEW** — no equivalent exists, see §1.3/R3 |
| Multi-zone geofencing | both | — | **GATED** — deferred with biometrics, `geo` captured but unenforced |
| Per-job-type geofence policy | tdu | — | **GATED**, same as above |
| Face-descriptor match (onboarding, ID-lookup, attendance) | both | `biometric_status`/`liveness_passed`/`match_distance` stub columns | **GATED** — extend the exact `attendance_events` stub pattern (§3.5), never live-wire without its own grant |
| Liveness (blink/head-turn) | both | same stub | **GATED** (also disabled-by-default in both legacy apps today — zero functional regression) |
| WebAuthn/passkeys, phone-OTP allowlist | both | — | **DECIDE / out of scope for v1** — matches `docs/onboarding-module-spec.md`'s own stated cut |
| Theory MCQ exam (author→approve→build→assign→take→grade) | both (adcda: SCDF, tdu: TDSCT) | `src/modules/exams` (generic) + `src/modules/tdsct` (station-reuse) | **LIVE** |
| AI question-authoring | n/a (platform-native) | `src/modules/exams` Studio | **LIVE** (fixture-replayed in demo; live-Claude path unconfirmed, see R9) |
| Practical rubric scoring (SCDF/TDSCT practical) | both | `practical_rubrics`/`practical_assessments` or `tdsct_practical_assessments` | **LIVE** — pick track per §1.3, don't blanket-assume |
| NFPA skill-sheet authoring + capture + sign-off | adcda | `src/modules/nfpa` (+ generic `exams` skill_sheets for non-NFPA tenants) | **LIVE, attributed port** — see R6 for the two-track trap |
| A/B/C practical-set rotation | adcda | `skill_sheet_sets`/`practical_set_assignment` | **LIVE** |
| Paper exam + QR + OMR grading fallback | n/a (platform-native, no legacy equivalent) | `src/modules/exams` paper track | **LIVE** — no legacy app has this; a platform-only capability both tenants inherit for free |
| BAPT physical-fitness OCR-grade-Sheets bot | adcda | — | **PORT-LOGIC** (grade.js rule engine only) — low priority, see §1.3 |
| Safety observations (capture→assign→worklog→close) | both | — | **BUILD-NEW** |
| Incident reports (bilingual accident/disciplinary) | both | — | **BUILD-NEW** |
| Equipment inspection, defect tracking, maintenance, charging log | both | `src/modules/assets` | **LIVE** |
| Recurring monthly test-run schedule | tdu | `asset_service_schedules` | **BUILD-EXT** — confirmed gap, see §1.3 |
| NEA regulatory monthly report | tdu | `buildMonthlyReport()` | **LIVE** |
| NEA external live read-access | tdu (aspirational) | `src/modules/assets/nea-access.ts` | **DECIDE** — designed, explicitly unwired, `NEA_EXTERNAL_ACCESS_ENABLED=false`; operator+cai call, see R7 |
| Staff HR roster + certificates | tdu | `src/modules/hr`, `staff`/`staff_certificates` | **LIVE** |
| Per-staff wages/allowance roll-up | tdu (unshipped in tdu itself) | `finance` functional role + `hr.wages.*`/`reports.wages.*` capabilities | **LIVE capability plumbing, BUILD-NEW UI/computation** — leapfrog opportunity, see §4.2 Phase 5 |
| Locked-down knowledge repo (no-copy viewer) | tdu | — | **BUILD-NEW**, low priority |
| Role/permission-matrix live editing | both (super_admin only in legacy) | Settings page | **LIVE**, `admin`-gated |
| Per-user `extraPermissions` overrides | adcda | `member` role + discrete capabilities | **BUILD-EXT** — see §1.4, don't reintroduce arbitrary per-user grants |
| Org module on/off toggles | n/a (platform-native) | `organizations.settings.modules` | **LIVE**, config-driven |
| Bilingual EN/AR + RTL | both (partial in both legacy apps) | i18n dictionaries + logical Tailwind classes | **LIVE, mechanically enforced** — stricter than either legacy app already |
| Offline-first PWA (3-tier cache) | both | — | **DECIDE** — Next.js App Router has a different offline story than Vite/Workbox; not evaluated in this pass, flag for a follow-on spec |
| Native iOS/Android (Capacitor) | both | — | **DECIDE / out of scope** — per-project rebuild cost either way, see R10 |
| Sentry/PostHog | both | — | **DECIDE** — not evaluated in this pass |

---

## 3. Residency-compliant data path

### 3.1 The three data realities that must never mix

1. **cosem-platform today** — 100% synthetic (`Meridian Training Academy`, `scripts/seed-demo.ts`).
   Confirmed repeatedly in migration comments ("SYNTHETIC data only — no real PII") and
   `docs/onboarding-module-spec.md:5` ("Data: SYNTHETIC trainees only — no real PII, no residency
   gate"). This is the *only* place demo/test/pilot work happens, full stop.
2. **Real ADCDA data** — UAE military-ID gov-PII, currently lives *only* in Firebase project
   `cosem-adcda-cb6d9` (`docs/data-store-registry.md`). Different jurisdiction from cosem-platform's
   current SG-region target.
3. **Real TDU data** — Singapore staff PII + new S$ wage figures, currently lives *only* in Firebase
   project `tdu-tools-prod`. Same jurisdiction (SG) as cosem-platform's current target, but is a
   *separate real client* with its own residency designation requirement.

### 3.2 The three-bucket rule (the operative policy for anyone building this)

| Data class | Where it may live | Gate required |
|---|---|---|
| Synthetic (any volume/realism) | The existing shared/demo Supabase project | None |
| Real, **same** jurisdiction as an already-designated silo | An additive `organizations` row in that silo | Explicit operator+cai **designation** in `docs/data-store-registry.md` before first write — same-region sharing still isn't a default, per the New-client rule's own wording ("temporary residency is how permanent commingling is born") |
| Real, **new/different** jurisdiction | A NEW Supabase project + deployment target, provisioned first | Full TENANT-RESIDENCY-001 sequence + registry update + (ADCDA specifically) the codebase's own `AGENTS.md` jurisdiction-siloing clause |
| Real biometric / enhanced-PII fields specifically | Same silo as the base grant, but the *columns* stay stubbed | A **separate, additional** PDPA + TENANT-RESIDENCY-001 grant, enforced at the schema level (stub-sentinel pattern, §3.5) — not just an app feature flag |

### 3.3 ADCDA — the UAE silo (mandatory, non-negotiable)

Order:
1. **Register the alias before anything else.** `docs/data-store-registry.md` gets a new row
   ("ADCDA silo (`<alias>`) — ADCDA (Abu Dhabi Civil Defence) — UAE region") the moment a project ref
   exists, per LAYER-VOCAB-001 (a data-write spec without a project ref is a review finding, same
   class as an unpinned migration).
2. Provision a **new Supabase project** in a UAE-compliant region (or nearest compliant region +
   an explicit, expiring residency-exception reasoning if UAE isn't directly offered — that
   reasoning itself needs the joint operator+cai grant).
3. A **separate Vercel deployment target** with its own env vars pointed at that project — mirrors
   ihsanos/ihsanos-irsyad exactly, not a novel pattern.
4. Replay the **identical migrations** (same schema, `AGENTS.md`'s own words) into the new project.
5. Pilot with **synthetic, ADCDA-flavored fixtures** (extend `scripts/seed-demo.ts`) end-to-end
   before any real row exists anywhere in this project.
6. **First real trainee batch is captured directly here** — never into `cosem-adcda-cb6d9` again.
   The Firebase app is frozen for new real writes the moment this happens.
7. Decommission `cosem-adcda-cb6d9` on a normal retention timeline (read-only archive for whatever
   audit-retention period applies — an operator/cai call — then teardown).

Because ADCDA is pre-live (§0), this sequence has **no live-cutover risk for trainee data** — it's a
race to finish before the first real batch, not a migration out from under active users. See §4.1.

### 3.4 TDU — same-jurisdiction designation (lighter-weight, still not a default)

TDU's real data can target the *same* ap-southeast-1 project cosem-platform already targets — SG
jurisdiction on both sides — **but still needs an explicit, recorded designation**, not silent
sharing. Two additional hygiene points specific to TDU:

- **Don't let the public showcase demo project double as TDU's real-data home.** `DEMO.md` frames
  `Meridian Training Academy` as a stage prop for external showcases (screenshots, live demos). Even
  with RLS isolating TDU's rows, a demo environment meant for external eyes and a production
  environment holding a real client's PII are different trust postures. Recommend a dedicated
  **production** Supabase project (still SG-region, still able to hold multiple real orgs later)
  separate from the public demo project — hygiene, not a jurisdiction requirement.
- **Verify the 216-block NEA billable-cap logic before any attendance cutover.** This design did not
  find that specific rule ported anywhere in the current attendance/assets tables — flag as a
  must-verify item, not an assumption, before Track B Phase 2 in §4.2.

### 3.5 The biometric / enhanced-PII sub-gate — extend the proven pattern, don't invent a new one

`attendance_events` already demonstrates the house style for a residency-gated field: real columns
exist (`biometric_status`, `liveness_passed`, `match_distance`), constrained to a safe sentinel enum
(`'capture_pending'`), populated `NULL`/unused, with a stub function (`src/modules/attendance/biometric.ts`
returning `'capture_pending'`) and a migration comment naming the exact grant that unlocks them
(`20260715120000_attendance_module.sql:6-11`). `src/modules/assets/nea-access.ts` demonstrates the
same discipline for an entirely different kind of gate (external data egress): a fully-designed,
three-layer (identity/capability/RLS) access model, an explicit `ENABLED = false` constant, and a
named list of open questions for the operator+cai decision — code that documents a decision boundary
instead of silently resolving it.

**Every new residency-gated field this port introduces should follow one of these two shapes exactly**:
face descriptors (onboarding match, ID-lookup, attendance), the full Emirates ID beyond the last 4
digits, and geofence *enforcement* (vs. capture) all get stub columns/sentinel functions/named grants
— never a live path opened "because the seam already exists and it's easy." §5 (risk R7) treats
premature wiring of an already-designed seam as a named, real risk, not a hypothetical one.

### 3.6 Immediate registry action (do this regardless of which track proceeds first)

`REPOS.json` and `docs/data-store-registry.md` currently record **no project ref** for
cosem-platform's existing SG-region target, even though a working demo clearly runs somewhere
(Vercel project `cosem-platform`/`cosem-platform-demo` is linked, CI runs DB tests). Confirm whether
that's a live-but-unregistered project or genuinely not-yet-provisioned, then update the registry
either way — a five-minute fix that unblocks every LAYER-VOCAB-001-compliant reference this document
and every future one needs to make.

---

## 4. Migration sequence (strangler order)

ADCDA and TDU are **not** the same kind of migration problem, and treating them identically would
either needlessly slow ADCDA or dangerously rush TDU. Two tracks:

### 4.1 Track A — ADCDA: "land before liftoff," not a live strangler

Because there is no real trainee data in production yet (§0), the goal is to **win the race to the
UAE silo before the first real batch lands**, not to migrate rows out from under active users.

1. **Provision the UAE silo** (§3.3 steps 1-4) — infra only, no app-code dependency.
2. **Close the functional gaps that would otherwise force ADCDA to stay on Firebase for its first
   real batch**: observations/case-management, incident reports, trainee/course roster attendance
   (§1.3, distinct from staff attendance), the 3 bilingual printables (port-as-is, cheap), and the
   `member`-role + `onboarding.manage`/`exams.skillsheets.author` capability additions so
   `local_instructor`/`skill_sheets_editor` have a real home (§1.4). Face-descriptors/liveness/full-ID/
   geofencing stay out (§3.5) — zero regression, since ADCDA already ships with liveness disabled.
3. **Pilot end-to-end with synthetic, ADCDA-flavored fixtures** in the UAE silo — full onboarding →
   NFPA skill-sheet capture → theory/practical exam → printable generation cycle, nothing real yet.
4. **First real trainee batch is captured directly into cosem-platform/UAE-silo.** `cosem-adcda-cb6d9`
   is frozen for new real writes at this exact moment.
5. Retire `pipelines/adcda-bot` by pointing its upsert step at the new platform (§1.3/§2), or
   formally sunset it if BAPT grading gets rebuilt natively — nice-to-have, not blocking.
6. Decommission `cosem-adcda-cb6d9` on a normal retention timeline.

### 4.2 Track B — TDU: genuine strangler-fig, phased, real users throughout

TDU is live with real staff and a real client relationship — this track needs gradual, reconciled
cutovers, ordered by risk (low-risk/already-live first) and by leverage (leapfrog the unshipped
wages feature rather than porting it twice).

- **Phase 0 — Foundation (no user-facing change).** Record TDU's residency designation (§3.4) in
  `docs/data-store-registry.md`; provision a dedicated production `organizations` row
  (`region='sg', type='company'`) in a project **separate from the public demo** (§3.4); point no
  real users at it yet.
- **Phase 1 — Shadow/parallel-run: People + HR.** One-time export of TDU's `regulars`+`staff`
  (Firestore) → platform's `staff`/`org_members`/functional-roles. Admins can *see* the roster/certs
  in cosem-platform while `tdu-tools-prod` stays system-of-record. Validates the ETL + RBAC mapping
  against real (if low-stakes) data with zero user-facing risk. **Land `staff.user_id` (risk R2)
  before this phase**, not after — it's needed for the export's identity-linking anyway.
- **Phase 2 — Cut over workforce attendance.** Already live, already non-biometric (matches TDU's
  own liveness-disabled-pending-calibration reality — no functional regression). Pilot on one
  team/store, verify the 216-block NEA billable-cap logic (§3.4) first, reconcile a full pay period
  against the old app's numbers, then widen.
- **Phase 3 — Cut over assets/inventory.** Already live. Build `asset_service_schedules` (§1.3 gap)
  before or during this phase — TDU's monthly test-run cadence is a real operational need. Pilot one
  store, reconcile a full inspection cycle, widen.
- **Phase 4 — Cut over TDSCT theory + practical.** Already live, reuses the exams grading engine.
  Lower urgency than attendance/assets (episodic, not continuous). Side-by-side score-parity test
  against a real recent TDSCT sitting before trusting a live cohort.
- **Phase 5 — Build + cut over what doesn't exist yet: observations, incident reports, wages.**
  **Wages is the leapfrog**: TDU's own wages feature is itself only "design-approved, implementation
  in progress" in the *old* app right now (per the cosem-tdu brief), and cosem-platform already has
  the capability plumbing waiting (`finance` functional role, `hr.wages.*`, `reports.wages.*`,
  unused). Finish it **once, natively, here** — don't ship it in Firestore this month and port it
  again next month.
- **Phase 6 — Decommission.** Once every real-data flow has run a full reconciliation cycle with
  zero discrepancies, freeze `tdu-tools-prod` to read-only, hold for an audit-retention window, then
  formally decommission (including any Capacitor/app-store implications, R10).

### 4.3 Cross-cutting phase-gate checklist (every phase, both tracks)

1. CI green (`npm run ci`) + the synthetic-test gate (flow×role matrix) on staging, synthetic data
   first — never real data as the first exercise of a new path.
2. A parallel-run reconciliation window on real (TDU) or pilot (ADCDA) data with a defined tolerance
   (zero-diff, ideally) before the old surface stops being system-of-record for that slice.
3. Explicit cai+operator go/no-go — this is real-client-data cutover, not routine feature work, per
   fleet doctrine on money/irreversible-adjacent gates.
4. Old app stays reachable **read-only** for a retention window post-cutover — never an abrupt hard
   cut. If a phase's reconciliation fails, that phase's traffic reverts to the old app while the rest
   of the sequence proceeds unaffected — failure is contained to one phase, never the whole cutover.

---

## 5. Top risks + mitigations

Ranked by (impact × how concretely the code substantiates it), highest first.

| # | Risk | Evidence | Mitigation |
|---|---|---|---|
| **R1** | **Residency gate defeated by ordering mistakes, not malice** — "just a few real rows to check the OCR mapping" against a misconfigured `.env.local` that's actually still pointed at the shared/demo project. | The New-client rule's own language predicts this exact failure mode ("temporary residency is how permanent commingling is born"). | A boot-time assertion in the UAE-silo deployment that refuses to start unless its own jurisdiction/project-ref env var matches an expected UAE marker — fail loud at boot, not quiet at first write. Mirrors the fleet's own `assert_no_sre_red_reset`/`assert_ops_only` pattern (`fleet_health_boundaries.py`). Process rule alongside the technical one: never seed the UAE-silo project with anything but synthetic fixtures, ever, even "just a few." |
| **R2** | **`staff` has no `user_id` link to `auth.users`.** `attendance_events_insert`'s RLS policy checks only `auth_user_has_capability(org_id,'attendance.mark')` — not "this `staff_id` belongs to the caller." Any `trainer`/`tc` grant-holder can currently self-mark a punch for a *different* `staff_id` at the app layer's discretion; the DB doesn't stop it. This is **weaker** than both legacy apps, which hard-enforced "matched descriptor must equal `linkedTrainerId`/`linkedRegularId`" before any self-attendance write. | `functions_roles_and_staff.sql:110-129` (full DDL read, no `user_id` column) vs. `trainee_login_accounts.sql` (added `trainees.user_id` on purpose). | Add `staff.user_id uuid references auth.users` (nullable, additive) now. Tighten `attendance_events_insert` (and the server action) to require `staff_id in (select id from staff where user_id = auth_user_id())` for `source='self'` rows specifically (kiosk/admin-sourced punches keep the capability-only check — those are operator-mediated, not self-asserted). Land before Track B Phase 2. |
| **R3** | **Trainee/course attendance silently assumed "already done."** `attendance_events` is staff-only (`staff_id references staff(id)`). ADCDA's original attendance surface was primarily about *trainees* (namelist-driven roster marking); a lane skimming "attendance module: LIVE" could either force trainees into `staff` (conflating two entities the migration's own comment explicitly forbids conflating) or ship ADCDA missing its most-used original feature. | `20260715120000_attendance_module.sql` DDL + `functional_roles_and_staff.sql:106-108` comment. | Explicit scope note (already in §1.3/§4.1) — batch/course/trainee attendance is a new, separate build item. Don't let "attendance: LIVE" in a status update mean more than it does. |
| **R4** | **`org_members.role` is free text, no DB CHECK.** A bulk-import of ~hundreds of real ADCDA/TDU people, or a future role addition (like the proposed `member`) that forgets to update `DEFAULT_ROLE_PERMISSIONS`, fails *silently* — `resolveEffectiveAccess` just returns `'none'` for an unrecognized role. Fails safe (not a privilege leak) but a real support/availability cost during a big import. | `role_rename.sql:10`, confirmed directly. | Add a CHECK constraint enumerating the final agreed role set as a deliberate Phase-0 migration (mirrors `role_rename.sql`'s own "collapse to fixed set" precedent) + a pre-import validator that rejects unrecognized-role rows before insert, not after. |
| **R5** | **Docs-drift-from-code is already recurring inside cosem-platform itself**, the exact failure mode the port is supposed to retire. `DEMO.md:72` says the skill-sheet transactional workflow is "deferred post-demo"; the NFPA migrations/code (dated after it) show it shipped. | Direct read of `DEMO.md` + `nfpa/eligibility.ts` + 3 dated migrations. | Fix `DEMO.md` now (cheap, zero-risk). More importantly: whoever executes §4 must verify against migrations/tests/running code, never against DEMO.md, this document, or any spec doc at face value, once time has passed — re-run `npm test` and exercise the flow live before trusting a "what's real" table, including this one's. |
| **R6** | **Two "skill sheet" tracks can be wired interchangeably by mistake** — exams module's generic `skill_sheets`/`skill_sheet_matrix` (older, non-eligibility-checked) vs. NFPA's `nfpa_skill_plans`/`skill_assessments` (the real adcda-attributed, eligibility-gated port). A lane skimming table names could wire real ADCDA/TDU capture against the wrong track, silently losing the same-day-retest/signed-pass-lock rule. | Confirmed by full `create table` grep across both migrations. | Name the distinction explicitly in any build spec (done here); consider a naming/comment cleanup pass so the tables themselves stop inviting confusion. |
| **R7** | **Premature wiring of an already-designed, deliberately-off seam** — `nea-access.ts` is fully shaped, `ENABLED=false`, with an explicit "DECISION REQUIRED (operator+cai)." Under TDU-port time pressure (a real client relationship, real urgency), there's a real temptation to "finish" it because the scaffold makes it look easy. | `src/modules/assets/nea-access.ts:1-83`, read in full. | Treat it as out of scope unless/until the decision is separately made. Flag to cai proactively if a client conversation raises it — don't resolve a named external-data-egress decision by building around it. |
| **R8** | **TDU's Firestore `staff` (their trainee-equivalent) and cosem-platform's `staff` (workforce/HR) are false friends** — same English word, opposite concepts. A careless port maps TDU's assessed cohort into platform's HR table (wrong: they need `trainees`) or platform's `staff` into a display label meaning "trainees" (wrong the other way). | Cross-referenced tdu's own collection description against `functional_roles_and_staff.sql`'s explicit "DISTINCT from `trainees`... do NOT conflate" comment. | Named explicitly in §1.3's mapping table. Anyone executing the TDU ETL should treat "staff" as ambiguous vocabulary requiring the source app to disambiguate every single time, not a search-and-replace. |
| **R9** | **AI authoring's live-Claude path is unconfirmed.** DEMO.md is explicit that the Studio replays fixtures ("zero live API calls, zero spend") — `.env.local.example` has the key plumbing but no live call path was confirmed in this pass. | `DEMO.md:17-19`, `.env.local.example` presence noted but not exercised. | Don't assume "AI authoring: LIVE" means "wired to a real model" for either tenant's real content-authoring needs — verify before promising it operationally. |
| **R10** | **Capacitor native apps are a separate, unscoped project.** Both legacy apps ship real iOS/Android wrappers; cosem-platform is web-only today. TDU has app-store-listed apps today — if native continuity is expected, that's bundle IDs/store listings/push entitlements, none of which this data/backend port touches. | Stated directly in both legacy apps' portNotes; no Capacitor config found in cosem-platform. | Raise explicitly with operator/client before assuming "TDU is fully migrated" once the backend port lands — PWA-parity-is-enough vs. native-required is a decision, not a default. |

---

## Immediate next actions (concrete, ordered)

1. Confirm/register cosem-platform's actual current Supabase project ref in `REPOS.json` +
   `docs/data-store-registry.md` (§3.6) — five minutes, unblocks every subsequent LAYER-VOCAB-001-compliant
   reference.
2. Land the two schema-hardening migrations independent of either track: `staff.user_id` (R2) and
   the `org_members.role` CHECK constraint (R4) — both small, both pure upside, both prerequisites
   for real-data work on either track.
3. Fix `DEMO.md:72` (R5) — zero-risk, immediate.
4. Decide, with cai/operator: does ADCDA's admin/super_admin split need to survive (§1.4 note), and
   is Track A (§4.1) or Track B (§4.2) the nearer-term priority given the operator's revenue-first
   posture and TDU's live client relationship vs. ADCDA's pre-live runway?
5. Build the `member` base role + `onboarding.manage`/`exams.skillsheets.author` capabilities
   (§1.4) — small, unblocks ADCDA's `local_instructor`/`skill_sheets_editor` mapping cleanly before
   either track needs it.
