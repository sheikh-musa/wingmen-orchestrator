# Data provenance — the one source of truth (op#25626, bus #51657/#51670)

Musa, 2026-10-05, after the fleet raised a false data-security P0 over
generated test data: *"we should have a way to know if data is synthetic or
not... a single source of truth that is always referenced first... a quran
for agents."*

**Before raising or dismissing ANY data-exposure/data-security claim, run:**

```
python3 scripts/data_truth.py classify <project_ref> [org_id]
```

**`org_id` is the FULL UUID** (the actual column value every real caller
has in hand — e.g. cosem-platform's `org_id UUID NOT NULL` column), not the
8-char short form this doc and bus messages use in prose for readability
(`1478c9b2` below means `1478c9b2-ff44-4091-a67e-a1391303c4ce`). Querying
with the short form will not match a registered row and silently falls
through to `UNCLASSIFIED` — exactly the false "unregistered" report
migration 093 fixed (bus #53416). `data_provenance.org_id` has a CHECK
constraint (migration 093) enforcing this: `''` (store-level default) or a
full UUID, nothing else.

This reads the `data_provenance` table (migration 089, orchestrator substrate
`tscuymavysscrvoberrr`) — see [`data-store-registry.md`](data-store-registry.md),
whose per-org table is generated FROM this table
(`scripts/gen_data_store_registry.py`), not hand-maintained.

## The trap this exists to close

**A slug or org name is NOT evidence of classification.** Cosem org `1478c9b2`
(`1478c9b2-ff44-4091-a67e-a1391303c4ce`) has slug `demo-academy` — no
`-synthetic` suffix — and is the org intended to hold REAL client data at
go-live. Its *current* trainee rows are synthetic
(`scripts/reseed-adcda-groups.ts`, commit `b7491a6`, cosem-platform repo).
Org `ba98da04` (`ba98da04-2a5e-46ba-97f8-387f17753bcc`), slug
`demo-academy-synthetic`, actually is fully synthetic.
Guessing from the name alone gets both of these backwards in opposite
directions. Classification comes only from a `data_provenance.evidence` field
that cites a concrete script/commit/migration/bus-message — never a string
match on a name.

## Classification states

| State | Meaning |
|---|---|
| `REAL` | Rows belong to a real client/tenant. TENANT-RESIDENCY-001 applies in full. |
| `SYNTHETIC` | Agent/script-generated, no real-data intent. |
| `MIXED` | The store holds both REAL and SYNTHETIC rows (store-level default when per-org rows aren't fully registered yet). |
| `MIXED_PENDING_REAL` | Current rows are SYNTHETIC, but this org/tenant is slated to receive REAL data at a go-live gate. Treat with the same care as `MIXED` until that gate clears — see [`GO-LIVE-CHECKLIST.md`](GO-LIVE-CHECKLIST.md). |
| `UNCLASSIFIED` | No `data_provenance` row exists. **Fail-safe: `treat_as_real()` is True.** Never treat an unregistered store/org as safe-because-probably-synthetic. |

## Fail-safe direction (orch-console gate condition #2, bus #51717)

`scripts/data_truth.py classify()` returns `UNCLASSIFIED` for an unregistered
project/org, and `Classification.treat_as_real()` is `True` for it — the same
as `REAL`, `MIXED`, and `MIXED_PENDING_REAL`. Only a registered, evidenced
`SYNTHETIC` row makes `treat_as_real()` `False`. Register the row (with
evidence) or escalate before acting on a claim about an unregistered store —
never assume "no row" means "safe."

## Markers convention (seed scripts, going forward)

New agent-generated/seed data should be self-identifying, so a future "is
this real?" question never needs provenance archaeology:

- An `is_synthetic boolean` column where the table has one.
- A visible `[SYN]` tag in generated display names.
- Synthetic emails on a `*.synthetic` or `*.test` domain, never a real-looking
  domain.

**Scope note:** this is fleet policy for new seed scripts from here forward.
Retrofitting existing seed scripts in ihsanos/cosem-platform/goumlyne with
these markers is a separate, per-repo fast-follow — not done as part of this
build (flagged explicitly, not silently dropped; see the design note, bus
#51716).

## irsyad / ihsanos client stores (orch-console gate condition #3, bus #51717)

On the irsyad silo (`goumlynecruxrlmzlntp`) and the ihsanos multi-tenant DB
(`ceayjeamtmcyzzvqflus`), the **store-level default is `REAL`**, and **no
org-level `SYNTHETIC` row is seeded without `cc-irsyad-coord`'s evidence and
sign-off** — fixture/wetprove-looking orgs on those stores have turned out to
be real clients before. If you think an org on either store is synthetic,
route the question to `cc-irsyad-coord`; don't classify it yourself.

## Enforcement

- `scripts/data_truth.py` — the lookup (see above). Importable as a library
  (`classify(project_ref, org_id=None) -> Classification`) or a CLI.
- `boot_briefing` carries a `data_provenance_flag` arm (added via
  `scripts/extend_boot_briefing_arm.py`, which builds the new view body from
  the LIVE `pg_get_viewdef()` at apply time — never a hardcoded copy, per
  decision 962) so every agent sees any `MIXED`/`MIXED_PENDING_REAL` row at
  boot, unprompted.
- `scripts/bus_send.py` warns (stderr, non-blocking, v1 per orch-console gate
  condition #4) on a P0/P1 message whose body contains data-security
  keywords without a classification citation.

## Correcting the record

Earlier docs/memory describing cosem org `1478c9b2` as simply "real" predate
this register and the op#25626 correction. `docs/data-store-registry.md`'s
CAI-RESP-1340 note is **not deleted** — it documents a real finding from
2026-07-09. It is annotated to point here: the org held real PII, was later
reseeded synthetic for the current demo/dev cycle, and is `MIXED_PENDING_REAL`
pending go-live. Both facts are true; neither supersedes the other.
