# Canonical Data-Store Registry (LAYER-VOCAB-001)

**Fleet doctrine — the ONLY valid way to name a data store.** Bare product names
("ihsanos", "cosem") are invalid as data references (LAYER-VOCAB-001). Always use
the alias, and in binding docs / gate requests / data-write reports, carry the
exact `project ref`. Layer-ambiguity in a data-path spec is a review FINDING, not
style (cai enforcement). Per-tenant data residency (TENANT-RESIDENCY-001): a
client's ROWS live in that client's designated store, always — shared code is
fine, commingled data is not.

**Per-org classification (generated section below):** whether a specific
*org*'s rows within a store are REAL or SYNTHETIC data is tracked in the
`data_provenance` table (migration 089), not here by hand — see
[`DATA-PROVENANCE.md`](DATA-PROVENANCE.md) and the generated table under
[Per-org classifications](#per-org-classifications-generated--do-not-hand-edit)
below. Before raising or dismissing a data-exposure claim, run
`scripts/data_truth.py classify <project_ref> [org_id]` — a slug or org name
is NOT evidence of classification (op#25626).

## Supabase projects

| Alias | Project ref | Tenant(s) / purpose | Region |
|---|---|---|---|
| **orchestrator substrate** (the monolith) | `tscuymavysscrvoberrr` | fleet substrate + non-ihsanos verticals | ap-southeast-2 (Sydney) |
| **ihsanos multi-tenant DB** | `ceayjeamtmcyzzvqflus` | ihsanos + org-scoped sub-tenants (default home for tenants w/o a silo) | ap-southeast-1 (SG) |
| **irsyad silo** (goumlyne) | `goumlynecruxrlmzlntp` | irsyad ONLY (tabung, DMS, school-fees, nasi-mandi donor data) — under Gazzabyte account | — |
| **wingmen-personal** | `brrgastulcffamlbggyu` | operator life-graph + Zahidah second-brain (mamadah) | ap-southeast-1 (SG) |
| **cosem-platform demo/dev** | `ywrpttpxwfcoodovxhsr` | shared DEMO + dev DB — meant synthetic-only, **NOT a home for real tenant data**. 🔴 **CAI-RESP-1340 VIOLATION (2026-08-31):** real ADCDA gov-PII (org `1478c9b2`, 69 real trainees — Emirates/military ID + DOB) has been living here since 2026-07-09, breaking CAI-525/711/809. CONTAINMENT in force: **no new real-PII writes to org `1478c9b2`** until a UAE-sovereign silo exists; reads/existing product op for the 69 NOT frozen; **no unilateral migrate/delete** (gated, mirrors CAI-812). Migration to a UAE-sovereign silo pending (investigation routed to orch-console). **CORRECTION (op#25626, 2026-10-05):** this finding describes 2026-07-09; org `1478c9b2`'s *current* trainee rows are a later SYNTHETIC reseed (`scripts/reseed-adcda-groups.ts` commit `b7491a6`, 2026-07-22+) — see the generated per-org table below and the `data_provenance` row. Both facts are true, neither supersedes the other. Current classification: `MIXED_PENDING_REAL` (see [`GO-LIVE-CHECKLIST.md`](GO-LIVE-CHECKLIST.md)). Do not read this row alone as "still real today." | ap-southeast-1 (SG) |
| **cosem-platform ADCDA silo** | _not yet provisioned — 🔴 migration target for the CAI-RESP-1340 real-PII in `ywrpttpxwfcoodovxhsr`_ | ADCDA real trainee data (Emirates-ID gov-PII) — MUST be its OWN UAE-**sovereign** silo (sovereignty ≠ region; AWS me-central-1 UAE only if sovereignty independently confirmed per CAI-809, never assumed from region name) before any further real write (TENANT-RESIDENCY-001). Migration plan + Musa sign-off on target pending. | UAE-sovereign (TBD) |
| **cosem-platform TDU silo** | _designation pending_ | TDU real staff/asset data — dedicated SG production org, never the demo project | ap-southeast-1 (SG) |

## Per-org classifications (generated — do not hand-edit)

<!-- GENERATED:data_provenance:START -->
| Project ref | Org | Classification | Evidence | Owner | Updated |
|---|---|---|---|---|---|
| `brrgastulcffamlbggyu` | (store-level) (wingmen-personal) | **REAL** | per docs/data-store-registry.md; never a demo target | orch | 2026-10-04 19:38:58.554635+00:00 |
| `ceayjeamtmcyzzvqflus` | (store-level) (ihsanos multi-tenant DB) | **REAL** | multi-tenant production DB per docs/data-store-registry.md; store-level default per orch-console gate condition #3 — any org-level SYNTHETIC row here requires cc-irsyad-coord evidence + sign-off, n... | orch | 2026-10-04 19:38:58.554635+00:00 |
| `goumlynecruxrlmzlntp` | (store-level) (irsyad silo (goumlyne)) | **REAL** | irsyad production silo per docs/data-store-registry.md; store-level default per orch-console gate condition #3 (bus #51717) — known-synthetic-looking orgs (e.g. QA Madrasah, QA Jumaat) are NOT seed... | irsyad-coord | 2026-10-04 19:38:58.554635+00:00 |
| `tscuymavysscrvoberrr` | (store-level) (orchestrator substrate) | **REAL** | fleet operational store (jobs/build_log/agent_messages/strategic_decisions) — not client data, no synthetic-vs-real ambiguity | orch | 2026-10-04 19:38:58.554635+00:00 |
| `ywrpttpxwfcoodovxhsr` | (store-level) (cosem-platform demo/dev) | **MIXED** | holds both synthetic-by-design demo orgs and org 1478c9b2 (see per-org row) — store-level default is MIXED, not REAL, precisely because of the 1478c9b2 case | cosem | 2026-10-04 19:38:58.554635+00:00 |
| `ywrpttpxwfcoodovxhsr` | Civil Defense Academy (cosem org 1478c9b2 (slug demo-academy)) | **MIXED_PENDING_REAL** | trainee rows (61+1) are SYNTHETIC: scripts/reseed-adcda-groups.ts commit b7491a6, run 2026-07-22 (+1 on 08-30), fixed FIRST/LAST name arrays, registers 101-830, seed-script header says "SYNTHETIC d... | cosem | 2026-10-06 01:08:33.676700+00:00 |
| `ywrpttpxwfcoodovxhsr` | Meridian Training Academy (Synthetic Demo) (cosem org ba98da04 (slug demo-academy-synthetic)) | **SYNTHETIC** | bus #51664/#51670: explicitly synthetic demo org, no real-data intent registered | cosem | 2026-10-06 01:08:33.676700+00:00 |

Generated by `scripts/gen_data_store_registry.py` — edit `data_provenance` (with evidence), not this block.
<!-- GENERATED:data_provenance:END -->

**mamadah routing note (PR #240, cc-quality MEDIUM):** content routing for the
`mamadah` channel to wingmen-personal is gated by
`nervous_system/personal_routing.PERSONAL_ROUTED_TAGS` in the orchestrator
substrate repo — **not** by `bot_channels.log_target`, which is read/stored
but does not drive any routing decision in code. Don't infer residency
behavior from `log_target`'s value for any channel; check
`PERSONAL_ROUTED_TAGS` instead.

## Firebase (cosem apps — separate stack)

| Alias | Firebase site / project | Tenant | Region |
|---|---|---|---|
| **cosem-adcda app** | `cosem-adcda-cb6d9` | ADCDA (Abu Dhabi Civil Defence) | not re-verified this pass |
| **cosem-tdu app (prod, default)** | `tdu-tools-prod` | TDU — production | asia-southeast1 (Singapore) — **confirmed** (op#22426, 2026-09-26) |
| **cosem-tdu app (staging)** | `tdu-tools-staging` | TDU — staging | **unverified** — PERMISSION_DENIED checking region (op#22426, 2026-09-26); do not retry or attempt to bypass, escalate instead |

### cosem-tdu subject data (schema/code review only — CAI-1034, no data files read)

Per op#22426 (project_governance onboarding for `cosem-tdu`). **TDU is a local
Singapore entity — there is no ADCDA involvement** (Musa op#22429, correcting an
earlier draft of this entry). Subjects are TDU (Singapore) **staff/trainees**: the
`staff` collection holds trainees, the `regulars` collection holds trainers/regular
staff (renamed from "trainers" in code, not in the UI). Sensitive fields found in
`firestore.rules` / `functions/index.js`:

- `faceDescriptor` — 128-element biometric face-embedding array on `regulars`, used for attendance face-match.
- `latitude` / `longitude` / `radiusMeters` — geofence definitions used for attendance check-in/out.
- `photoUrl` — observation/incident photos.
- `phoneNumber` / `phone` — E.164, used for OTP auth and `phoneAllowlist` (admin-only allowlist of who may register).
- `idNumber` / `emiratesId` — UAE Emirates ID + OCR extraction fields, **inherited from the shared cosem/ADCDA codebase** (`784-####-#######-#` pattern parsing) — present in schema, not expected in use for a SG entity; unverified whether TDU's product flow ever populates them.
- `dob`, `enName`, `arName` — also OCR-extracted alongside the ID number; same inherited-from-ADCDA, unverified-for-TDU caveat applies.
- Attendance/ops collections: `staffAttendanceEvents` (server-write-only, audit trail), `attendanceSessions`, `incidentReports` (type/description/caseStatus/queue, trainer+ role write access), `scdf_theory_results` / `scdf_practical_results`.
- Wages are **derived** from attendance-hours roll-ups, not a raw stored salary field (none found).

## Layers of a shared product (say which one)

- **frontend** — the shared, generalized app codebase (one repo, all tenants).
- **data** — always name the store above + its `project ref`; never bare "ihsanos".

## New-client rule (TENANT-RESIDENCY-001)

A new client's silo is provisioned or explicitly designated **before the first
client data write** — never "temporarily" in a shared project. Temporary
residency is how permanent commingling is born. Residency exceptions require a
joint operator + cai grant and must expire.
