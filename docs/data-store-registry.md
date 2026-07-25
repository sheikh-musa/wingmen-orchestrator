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
| **irsyad silo** (goumlyne) | `goumlynecruxrlmzlntp` | irsyad ONLY (tabung, DMS, school-fees, nasi-mandi donor data). **irsyad = the CLIENT; Gazzabyte = the PARTNER who handles irsyad; goumlyne is Gazzabyte's silo holding irsyad's data** (see Partners & clients below) | — |
| **wingmen-personal** | `brrgastulcffamlbggyu` | operator life-graph + Zahidah second-brain (mamadah) | ap-southeast-1 (SG) |

## Firebase (cosem apps — separate stack)

| Alias | Firebase site / project | Tenant |
|---|---|---|
| **cosem-adcda app** | `cosem-adcda-cb6d9` | ADCDA (Abu Dhabi Civil Defence) |
| **cosem-tdu app** | `tdu-tools-prod` | TDU / NEA |

## Partners & clients (who is who — do NOT conflate)

A **partner** is an agency/intermediary who brings and handles one or more **clients**;
the client is the end-org whose data we hold. Name the layer precisely — a partner is
not a client, and a silo belongs to whoever owns the account, not to the end-client.

| Partner | Client(s) they handle | Data store (silo) |
|---|---|---|
| **Gazzabyte** (partner) | **irsyad** (tabung/donations, DMS, school-fees, nasi-mandi) | irsyad silo (goumlyne) `goumlynecruxrlmzlntp` — Gazzabyte's account |
| _(direct — no partner)_ | TDU / NEA | cosem-tdu app `tdu-tools-prod` |
| _(direct — no partner)_ | ADCDA (Abu Dhabi Civil Defence) | cosem-adcda app `cosem-adcda-cb6d9` |

> The `clients` table (orchestrator substrate) is currently a FLAT list (e.g.
> "Gazzabyte" as one row) and does NOT model partner→client→silo — that gap is why
> the relationship kept getting mis-stated. This table is the authoritative record
> until the substrate models it properly. Add a mapping row only after the operator/cai
> confirm it; do not invent partner/client relationships.

## Layers of a shared product (say which one)

- **frontend** — the shared, generalized app codebase (one repo, all tenants).
- **data** — always name the store above + its `project ref`; never bare "ihsanos".

## New-client rule (TENANT-RESIDENCY-001)

A new client's silo is provisioned or explicitly designated **before the first
client data write** — never "temporarily" in a shared project. Temporary
residency is how permanent commingling is born. Residency exceptions require a
joint operator + cai grant and must expire.
