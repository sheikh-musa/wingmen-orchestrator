# Canonical Data-Store Registry (LAYER-VOCAB-001)

**Fleet doctrine — the ONLY valid way to name a data store.** Bare product names
("ihsanos", "cosem") are invalid as data references (LAYER-VOCAB-001). Always use
the alias, and in binding docs / gate requests / data-write reports, carry the
exact `project ref`. Layer-ambiguity in a data-path spec is a review FINDING, not
style (cai enforcement). Per-tenant data residency (TENANT-RESIDENCY-001): a
client's ROWS live in that client's designated store, always — shared code is
fine, commingled data is not.

## Supabase projects

| Alias | Project ref | Tenant(s) / purpose | Region |
|---|---|---|---|
| **orchestrator substrate** (the monolith) | `tscuymavysscrvoberrr` | fleet substrate + non-ihsanos verticals | ap-southeast-2 (Sydney) |
| **ihsanos multi-tenant DB** | `ceayjeamtmcyzzvqflus` | ihsanos + org-scoped sub-tenants (default home for tenants w/o a silo) | ap-southeast-1 (SG) |
| **irsyad silo** (goumlyne) | `goumlynecruxrlmzlntp` | irsyad ONLY (tabung, DMS, school-fees, nasi-mandi donor data) — under Gazzabyte account | — |
| **wingmen-personal** | `brrgastulcffamlbggyu` | operator life-graph + Zahidah second-brain (mamadah) | ap-southeast-1 (SG) |
| **cosem-platform demo/dev** | `ywrpttpxwfcoodovxhsr` | shared DEMO + dev DB — meant synthetic-only, **NOT a home for real tenant data**. 🔴 **CAI-RESP-1340 VIOLATION (2026-08-31):** real ADCDA gov-PII (org `1478c9b2`, 69 real trainees — Emirates/military ID + DOB) has been living here since 2026-07-09, breaking CAI-525/711/809. CONTAINMENT in force: **no new real-PII writes to org `1478c9b2`** until a UAE-sovereign silo exists; reads/existing product op for the 69 NOT frozen; **no unilateral migrate/delete** (gated, mirrors CAI-812). Migration to a UAE-sovereign silo pending (investigation routed to orch-console). | ap-southeast-1 (SG) |
| **cosem-platform ADCDA silo** | _not yet provisioned — 🔴 migration target for the CAI-RESP-1340 real-PII in `ywrpttpxwfcoodovxhsr`_ | ADCDA real trainee data (Emirates-ID gov-PII) — MUST be its OWN UAE-**sovereign** silo (sovereignty ≠ region; AWS me-central-1 UAE only if sovereignty independently confirmed per CAI-809, never assumed from region name) before any further real write (TENANT-RESIDENCY-001). Migration plan + Musa sign-off on target pending. | UAE-sovereign (TBD) |
| **cosem-platform TDU silo** | _designation pending_ | TDU real staff/asset data — dedicated SG production org, never the demo project | ap-southeast-1 (SG) |

## Firebase (cosem apps — separate stack)

| Alias | Firebase site / project | Tenant |
|---|---|---|
| **cosem-adcda app** | `cosem-adcda-cb6d9` | ADCDA (Abu Dhabi Civil Defence) |
| **cosem-tdu app** | `tdu-tools-prod` | TDU / NEA |

## Layers of a shared product (say which one)

- **frontend** — the shared, generalized app codebase (one repo, all tenants).
- **data** — always name the store above + its `project ref`; never bare "ihsanos".

## New-client rule (TENANT-RESIDENCY-001)

A new client's silo is provisioned or explicitly designated **before the first
client data write** — never "temporarily" in a shared project. Temporary
residency is how permanent commingling is born. Residency exceptions require a
joint operator + cai grant and must expire.
