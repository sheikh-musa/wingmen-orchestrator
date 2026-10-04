# Go-live checklist — cosem org `1478c9b2` (Civil Defense Academy)

Referenced from [`DATA-PROVENANCE.md`](DATA-PROVENANCE.md) and the
`data_provenance` table (migration 089) as the gate for flipping this org's
classification from `MIXED_PENDING_REAL` to `REAL`.

This org (cosem-platform `ywrpttpxwfcoodovxhsr`, slug `demo-academy`) is the
real-intended client. Its current trainee rows are a synthetic reseed
(`scripts/reseed-adcda-groups.ts`, commit `b7491a6`, op#25626). Before real
trainee data is allowed back in:

- [ ] Remove one-click/demo logins for this org (bus #51670 — the specific
      blocker named for this gate; cc-cosem-platform owns verifying this).
- [ ] Confirm no synthetic trainee rows remain commingled with real ones at
      cutover (a clean reseed-then-cutover, not a gradual real/synthetic mix).
- [ ] Update the `data_provenance` row for `('ywrpttpxwfcoodovxhsr',
      '1478c9b2')` to `classification = 'REAL'`, with `evidence` citing the
      cutover commit/migration — via a normal UPDATE, owner `cosem`, not by
      deleting and re-inserting the row (preserve the audit trail).
- [ ] Re-run `scripts/gen_data_store_registry.py` so
      `docs/data-store-registry.md` reflects the flip.

Until every box above is checked, treat this org's data path with the same
care as `MIXED` (TENANT-RESIDENCY-001 applies to any real rows once they
land) — do not downgrade the classification early on the strength of a
go-live date alone.
