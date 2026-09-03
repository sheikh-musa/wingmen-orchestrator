# ihsanos-prod audit-chain re-derivation (CAI-RESP-1364)

Read-only re-derivation of the ihsanos-prod (ceayj, multi-tenant) audit_log chains, run under
CAI-RESP-1364 (same tooling as the goumlyne frozen list). **It surfaced more than the goumlyne
content gap: ihsanos-prod also has LINKAGE breaks — a distinct, more serious class the frozen-list
amnesty does NOT and must NOT cover.**

## Re-run

```
# step 1 (RO dump, multi-tenant, with org_id) — inline query, GITIGNORED output (payloads):
#   SELECT id, org_id, prev_hash, hash, payload, entity_type, action
#     FROM public.audit_log WHERE id<=<tip> ORDER BY org_id, id ASC   (IHSANOS_PROD_RO_DATABASE_URL)
#   -> chain.jsonl + snapshot_meta.json
node rederive_frozen_list.mjs   # per-org: linkage check + content-non-repro frozen list (exact hashchain.ts)
node diagnose_linkage.mjs       # classifies each linkage break (fork / sentinel / dangling)
```

## Snapshot (tip 2878, 2024 rows, 16 orgs)

Three orgs have findings; the other 13 are clean:

| org | rows | content non-repro | linkage | nature |
|-----|------|-------------------|---------|--------|
| `5abe01ce…` | 27 | 26 | **INTACT** | clean reporting-gap (like goumlyne) — in-RPC writers tabung_umum_tin/create, donation_categories, bank_keyword_mappings. **Frozen-list amnesty is correct here.** |
| `25f933d8…` | 1548 | 0 | **39 BREAKS** | the **synthetic QA fixture** ("QA Shop (synthetic)", CAI-RESP-1356) — NOT a real tenant; row-count is not tenancy. **concurrent-write FORKS** from the app-path `writeAuditLog` read-then-insert race (prev_hash → row i‑2; two rows off the same tip). NOT tamper, NOT amnesty-able. The 33→39 growth is CAI-1356's already-routed known bug accumulating under synthetic load; central fix owned by cc-storefront (must use the convergence lock key `hashtextextended(org_id::text,0)`). |
| `e9b3f7f9…` | 117 | 50 | 1 break | 🔴 **BAPA — a REAL CLIENT** (corrected per CAI-RESP-1366 after cc-ihsanos flagged an earlier mis-ID). 50 content non-repro (in-RPC sch_fee/inv_invoice/inv_payment) PLUS an **open, unexplained linkage anomaly** at id 2528 (dangling prev_hash). id 2528 is **excluded** from the content frozen-list; the chain reads BROKEN at 2528 until resolved. Root-cause routed to **cc-ihsanos** (owns ihsanos-platform) — NOT "low concern". |
| `00000000…` | 135 | 135 | broken | **NOT a hash chain** — platform/system event log with sentinel prev_hash (`SUPER_ADMIN`, `MIGRATION_05`). Exclude from chain verification/boundary entirely. |

## Disposition (per CAI-RESP-1365 / 1366)

- **Content amnesty AUTHORED** — substrate `audit_chain_boundaries` id=4 (org `5abe01ce`, 26 ids, sha 241f5e43…) + id=5 (org `e9b3f7f9`=BAPA content rows, 49 ids, id 2528 excluded, sha adb04fc4…). Frozen-list, same mechanism as goumlyne. In-code mirror = part of cc-storefront's ihsanos-prod verifier rollout on the shared Track-A mechanism.
- **Linkage forks** (org `25f933d8`, the synthetic QA fixture): the shared-core `writeAuditLog` read-then-insert race — CAI-1356's standing fork-fix, owned by cc-storefront. Central fix must lock on the convergence key `hashtextextended(org_id::text,0)`. NOT amnesty-able (linkage breaks correctly read BROKEN).
- **`e9b3f7f9` id 2528** (BAPA, real client): open linkage anomaly, root-cause routed to **cc-ihsanos** (CAI-1366).
- **System org `00000000`**: excluded from hash-chain verification (sentinel log, not a chain).

`chain.jsonl` carries audit payloads → gitignored, never committed. Committed: the scripts + the
payload-free `frozen_amnesty_ihsanos_prod.json` + `snapshot_meta.json`.
