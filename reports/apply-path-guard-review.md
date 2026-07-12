# Apply-path immutability guard — focused review set (CAI-RESP-420 #50, Deliverable C)

For operator review to flip **MIGRATION-1 → COVERED**. Branch `feat/preventative-gates`
(tip `b199351`), merge target = **canonical `feat/operator-telegram-bridge`** (NOT main).
Nothing merged; nothing in the live fleet apply path calls this yet.

## Review scope — exactly 3 files (ignore the canonical delta)
| File | Role | Anchors |
|---|---|---|
| `migrations/023_migration_ledger.sql` | the ledger table | `:10` CREATE, `:17` PK `(repo, migration_name, silo_ref)` |
| `scripts/gates/migration_immutability_guard.py` | the guard | `_recorded :54`, `check_one :83`, `record :106`, `assert_repo :125`, `LedgerUnavailable :76` |
| `scripts/apply_sql_migration.py` | the apply entrypoint it wraps | `check_one :59`, fail-closed abort `:64`, `record :80`, bootstrap flag `:45` |

## (a) The ledger
```sql
CREATE TABLE migration_ledger (
  repo TEXT, migration_name TEXT, silo_ref TEXT, sha256 TEXT,
  applied_at TIMESTAMPTZ DEFAULT now(), applied_by TEXT,
  PRIMARY KEY (repo, migration_name, silo_ref)   -- per-silo: same migration must be byte-identical across silos
);   -- service-role-only RLS
```

## (b) The hook — what wraps the apply (scripts/apply_sql_migration.py)
```python
with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    # GATE (before write): refuse an amended body; FAIL CLOSED if ledger unreadable.
    try:
        guard.check_one(conn, "orchestrator", mig_name, SUBSTRATE_REF, path,
                        allow_missing_ledger=allow_missing)
    except guard.ImmutabilityViolation as e:
        print(f"ABORT — immutability violation: {e}"); return 1
    except guard.LedgerUnavailable as e:
        print(f"ABORT — FAIL CLOSED, ledger unavailable (will not apply unverified): {e}"); return 1
    ... apply DDL statements + schema_migrations tracker ...
    conn.commit()
    guard.record(conn, "orchestrator", mig_name, SUBSTRATE_REF, path)   # LEDGER (after write)
```
Entrypoint wrapped: **only** `scripts/apply_sql_migration.py` (the generic direct-psycopg
applier added this branch), targeting the **substrate** (`SUBSTRATE_REF=tscuymavysscrvoberrr`).
The pre-existing per-migration `apply_*.py` scripts are NOT wrapped; no live path calls this yet.

## (c) The guard core — FAIL CLOSED (this was a fix from your review; it previously failed OPEN)
```python
def _recorded(conn, repo, name, silo_ref):
    if not _ledger_exists(conn):
        raise LedgerUnavailable("migration_ledger table not present — cannot verify migration immutability")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sha256 FROM migration_ledger WHERE repo=%s AND migration_name=%s AND silo_ref=%s",
                        (repo, name, silo_ref))
            row = cur.fetchone()
    except psycopg.Error as e:
        raise LedgerUnavailable(f"migration_ledger read failed: {e}") from e   # fail closed
    return row[0] if row else None

def check_one(conn, repo, name, silo_ref, path, allow_missing_ledger=False):
    cur_hash = sha256_file(path)
    try:
        rec = _recorded(conn, repo, name, silo_ref)     # None => never applied (NEW)
    except LedgerUnavailable:
        if allow_missing_ledger:
            return None        # documented bootstrap: this migration CREATES the ledger
        raise                  # FAIL CLOSED — cannot verify, refuse to apply
    if rec is not None and rec != cur_hash:
        raise ImmutabilityViolation(...)   # MUTATED applied migration -> abort
    return None
```
Fail-closed behavior:
- ledger table absent OR read error -> `LedgerUnavailable` -> apply ABORTs, CI `--check` exits nonzero. Never silently skipped.
- DB fully unreachable -> `psycopg.connect` fails -> apply can't start (fail closed by construction).
- Only sanctioned pass on a missing ledger: explicit `--allow-missing-ledger`, used once to bootstrap the ledger-creating migration itself.
- Tests: `test_missing_ledger_FAILS_CLOSED`, `test_missing_ledger_bootstrap_escape_hatch`.

## (d) NEW vs MUTATED — the discriminator is the ledger row for (repo, migration_name, silo_ref)
- **No row** -> never applied -> NEW -> `check_one` returns None, apply proceeds, `record` inserts the hash.
- **Row, hash == file** -> unchanged body -> allowed (idempotent re-apply).
- **Row, hash != file** -> MUTATED applied migration -> `ImmutabilityViolation` -> abort (the 061->092 case).
Verified end-to-end on real substrate: in-place edit of an applied migration -> HARD FAIL; restore -> OK.

## Product-repo scope — orchestrator-repo ONLY right now (the key limitation)
Covers **only orchestrator migrations -> substrate** (the 21 backfilled). Does NOT yet cover
**ihsanos or cosem** applies — and 061 lives in **ihsanos**, applied to ceayj + goumlyne, which
don't route through `apply_sql_migration.py`. The `repo` column exists to support them; nothing
records/checks `ihsanos` until its apply path + CI are wired. That cross-repo piece is routed to
cc-ihsanos (operator's call): drop-in `artifacts/ihsanos-ci/immutability-check.yml` + backfill
ihsanos hashes per silo. Until then, the drift-detector is the backstop that CATCHES the resulting
drift after the fact; the guard PREVENTS it at apply time only for orchestrator.

## Recommendation for MIGRATION-1 gate_status
MIGRATION-1 is dual-gated: (i) drift-detector (schema byte-identical across silos — LIVE, currently
finding real goumlyne drift) + (ii) this immutability guard (file-body immutability — LIVE for
orchestrator, pending for ihsanos). Suggest COVERED only once BOTH are green for the silos in scope;
today MIGRATION-1 honestly stays `pending` (goumlyne is drifted + ihsanos apply-path not yet wired).
cai owns the final gate_status semantics (open question #7758).
