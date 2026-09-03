# Audit-chain amnesty — frozen row-id list (CAI-RESP-1363)

Re-runnable source of truth for the goumlyne (irsyad silo) audit-chain content-verification amnesty.

## Why this exists

The in-RPC SQL audit appenders (mig309 `person/update`, mig325 BC-load, tabung tin RPCs, FAS
`sch_fas_scheme`) hash `jsonb_build_object(...)::text` (pg: length-ordered keys, spaces), which is
**not** byte-identical to the app-side `canonicalPayloadJson` = `JSON.stringify(payload, keys.sort())`
(alphabetical, no spaces). So those rows do not *content*-reproduce under `src/shared/lib/hashchain.ts`
`verifyChainIntegrity`, and above the static id=742 cutover they scored **BROKEN** on client-facing
surfaces (audit dashboard / compliance export) — a live **false** signal. **Linkage is intact** (the
insert/delete tamper-evidence holds); only content-reproducibility is affected.

cai ruled the fix (CAI-RESP-1362 / **CAI-RESP-1363**): reclassify those rows as `UNVERIFIABLE_PRE_FIX`,
not BROKEN — but keyed to an explicit **frozen ROW-ID LIST**, not an `(entity_type,action)` class.
The class key would over-amnesty: 3 of 4 offender classes are *mixed* (both app-reproducing and in-RPC
non-reproducing rows — e.g. `person/update` 39/1678, `tabung_umum_tin/update` 269/778), so a class-level
amnesty would mask a genuine tamper on a currently-reproducing app row. The frozen row-id list closes that
gap: amnesty fires **iff** a row's (immutable) id is on the list; every other row keeps BROKEN-on-mismatch
forever.

## The mechanism

`verifyChainIntegrity` amnesties a **content mismatch** iff `rowId ∈ frozen list`. A reproducing row never
reaches the mismatch branch, so it is never amnestied. The 6 below-cutover *reproducing* pre-fix rows
`[93,95,96,97,733,734]` are deliberately **excluded** from the list (they keep BROKEN-on-tamper) — so the
frozen list is complete and can be the **sole** determinant, stricter than the old `id<742` blanket arm.

## Re-derivation (independently re-runnable)

```
python rederive_dump.py            # step 1: dump chain + snapshot_meta.json from GOUMLYNE_RO_DATABASE_URL @ tip
node   rederive_frozen_list.mjs    # step 2: EXACT hashchain.ts computeHash -> frozen_amnesty_goumlyne.json
```

Node is **required** for step 2: `JSON.stringify(payload, keys.sort())` applies the array replacer as a
nested-key allowlist at every level; a Python `sort_keys` approximation would false-flag nested payloads.

`goumlyne_chain.jsonl` (step-1 output) carries audit **payloads** and is **gitignored** — regenerate it,
never commit it. Committed here: the two scripts, `snapshot_meta.json`, and `frozen_amnesty_goumlyne.json`
(ids + counts + snapshot metadata, payload-free).

## Source of truth & mirror

- **Substrate (citable):** orch substrate `audit_chain_boundaries` row id=1 — `amnesty_mechanism='row-id-list'`,
  `amnesty_row_id_snapshot_at`, `amnesty_snapshot_tip_id`/`_hash`, `amnesty_row_id_count`, `amnesty_row_ids[]`,
  `amnesty_rederivation_ref` (points here). Authored by orch-console under CAI-RESP-1363.
- **In-code mirror (what the verifier uses):** `src/shared/lib/verifiability-boundaries.ts` mirrors the id list;
  a boundary-matches-substrate tripwire test asserts they agree (the client silo cannot read the substrate at runtime).

## Snapshot (this freeze)

- org: `73339164-7c1f-40ba-a093-33f1f292dd4c` (goumlyne), tip_id **33530**, 8490 rows ≤ tip.
- frozen (non-reproducing) rows: **3100** (ids 98..33529). Linkage intact (zero breaks).

## Going-forward cadence

Rows written **after** the snapshot tip by still-unconverted in-RPC writers will not be on the list and will
read BROKEN until the list is re-extended. Re-run the two scripts and append on a documented cadence **until
Track B** (a shared canonical SQL serializer) converts each writer class — `person/update` first (identity
chain + largest offender). Each converted writer's post-conversion rows content-reproduce and drop off the list.
