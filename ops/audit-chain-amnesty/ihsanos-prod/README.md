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
| `25f933d8…` | 1548 | 0 | **39 BREAKS** | **concurrent-write FORKS** — app-path `writeAuditLog` read-then-insert race (prev_hash points to row i‑2; two rows chain off the same tip). NOT tamper. NOT amnesty-able. Needs a code fix (atomic/advisory-locked writeAuditLog — the fix the 077 RPC already embodies). |
| `e9b3f7f9…` | 117 | 50 | 1 break | fixture/test org — 1 dangling prev_hash (id 2528 → no in-org row) + content non-repro; test-data artifact, low concern. |
| `00000000…` | 135 | 135 | broken | **NOT a hash chain** — platform/system event log with sentinel prev_hash (`SUPER_ADMIN`, `MIGRATION_05`). Exclude from chain verification/boundary entirely. |

## Disposition (pending cai + cc-storefront, the domain owner)

- **Content amnesty** (org `5abe01ce`, and the content rows of `e9b3f7f9`): frozen-list, same mechanism as goumlyne — author once cai/cc-storefront direct.
- **Linkage forks** (org `25f933d8`): a SEPARATE finding — the shared-core `writeAuditLog` read-then-insert race. A code fix (make the tip-read+insert atomic under a per-org advisory lock, like the RPC path), owned by cc-storefront/hub. The amnesty cannot and must not hide it (linkage breaks correctly read BROKEN).
- **System org `00000000`**: exclude from hash-chain verification (sentinel log).

`chain.jsonl` carries audit payloads → gitignored, never committed. Committed: the scripts + the
payload-free `frozen_amnesty_ihsanos_prod.json` + `snapshot_meta.json`.
