# Wingmen orchestrator migration conventions

Authoritative reference for how this repo writes & applies Supabase migrations.

## TL;DR

1. Additive-only at the live-DB layer. Destructive ops require cai review.
2. **Never** `supabase db push` against production. Use direct psycopg apply.
3. Populate `supabase_migrations.schema_migrations.statements` so the ledger is forensically useful.
4. Every migration ends with an assertion gate.

## Why these rules exist

Two substrate-drift incidents in May 2026 forced this document:

- **2026-05-22 boot_briefing arm-loss** — `supabase db push` re-applied historic `CREATE OR REPLACE VIEW boot_briefing` statements from migrations whose view bodies pre-dated newer arms, silently stripping `active_autonomous_loops` and `long_running_caller` from the live view. Root cause + mitigations in CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS (decision 962).
- **cc-scholar runaway 2026-05-15** — a forgotten daemon burned ~155M tokens over 10 days because we had no per-CC-family token visibility. Token tracking restored via CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME (decision 964).

Both incidents traced back to substrate observability gaps. The conventions below are the structural fix.

## Convention 1: Additive-only at the live-DB layer

The `scripts/check_additive_migration.py` linter is the gate. It rejects DROP / TRUNCATE / ALTER COLUMN DROP / blanket UPDATE/DELETE.

If a migration genuinely needs a destructive op, get cai review first, then add `-- linter-allow: <reason>` to opt out.

## Convention 2: Never `supabase db push` against production

The CLI's shadow-diff mechanism re-applies historic CREATE OR REPLACE statements, breaking idempotency assumptions.

Use the direct psycopg apply pattern (committed in PR #41 / #42 / #44):

```python
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('supabase/migrations/20260527_cc_session_costs_cache_tokens.sql').read()
with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
    cur.execute(sql)
```

The linter's M_PRIMARY warning will fire if it detects the supabase CLI as the parent process.

## Convention 3: Populate `schema_migrations.statements`

Every migration ends with:

```sql
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('YYYYMMDDHHMMSS', 'migration_name', ARRAY[
    $stmt$
    <full SQL body of the migration goes here, escaped via $-quotes>
    $stmt$
]::text[])
ON CONFLICT (version) DO NOTHING;
```

The historical pattern of `ARRAY[]::text[]` (empty array) is **DEPRECATED**. Future migrations MUST include the SQL body for forensics. Without this, if the live DB diverges from the on-disk migration files, we lose the post-mortem evidence trail.

If a migration legitimately can't include its body (e.g., body contains the `$stmt$` delimiter), document the reason in a comment + use `ARRAY[]::text[]` as a fallback.

## Convention 4: Assertion gate

Every migration ends with a `DO $$ ... END $$` block that verifies the migration's intended effect (column exists, view contains expected substring, etc.) and `RAISE EXCEPTION` if not. This rolls back the transaction on partial failure.

See `supabase/migrations/20260521_active_loops_parent_pid.sql` for the canonical pattern.

## Linter advisories

`scripts/check_additive_migration.py` emits **non-blocking** advisories for:

- Empty `statements` array (convention 3 violation; recommend populating)
- `supabase` parent process (convention 2 violation; loud warning to stderr)

Advisories don't block the apply — they're operator-visible signals. A blocking advisory upgrades the cost of opt-out enough that future ops respect the convention.

## Convention 5: Privilege-revoke assertions (CAI-RESP-1397 #5)

A `REVOKE` run by a role that never granted the privilege it's targeting is
a **silent no-op** — verified against a real Postgres: every new function
grants EXECUTE to PUBLIC by default, so `REVOKE EXECUTE ON FUNCTION f()
FROM anon` when `anon` was never granted it *directly* has nothing of
anon's to remove; PUBLIC still makes the function fully executable, and the
migration "succeeds" having changed nothing. cc-quality wet-proved exactly
this class of defect on a real migration.

`scripts/apply_migration.py` now enforces the fix in code:

```sql
-- assert: no_execute anon public.fetch_and_execute_sql(text)
-- assert: search_path public.get_decision(text)
-- assert: dropped public.fetch_and_execute_sql(text)
```

One `-- assert:` line per (role, function) pair, checked **inside the same
transaction**, immediately after the migration body runs, before the
ledger insert:

| kind | passes when |
|---|---|
| `no_execute` | `has_function_privilege(role, fn, 'EXECUTE')` is `false` |
| `search_path` | `pg_proc.proconfig` for `fn` has an entry starting `search_path=` |
| `dropped` | `to_regprocedure(fn) IS NULL` |

Any assertion failing rolls back the whole migration and names the exact
pair that failed — a privilege-revoke migration can no longer "succeed"
without proving its effect. **Required, not optional:** a migration body
containing `REVOKE` or `DROP FUNCTION` with zero `-- assert:` lines refuses
to apply at all.

## Backfill policy

The 5 migrations that currently use `ARRAY[]::text[]` are NOT being backfilled (would require diffing committed migrations against `pg_get_viewdef` for each one). New migrations forward-apply the convention.

| migration | populated statements |
|---|---|
| `20260416_arch019_boot_briefing_index.sql` | N (legacy) |
| `20260509_boot_briefing_substrate_visibility.sql` | N (legacy) |
| `20260517_active_autonomous_loops.sql` | N (early Phase A) |
| `20260517_long_running_claude_callers.sql` | N (early Phase A) |
| `20260521_active_loops_parent_pid.sql` | N (Phase B Task 1) |
| `20260523_watchdog_monitored_callers.sql` | N (Phase B Task 4) |
| `20260527_cc_session_costs_cache_tokens.sql` | N (token-track Task 1) |
| _all future migrations_ | **Y (convention 3)** |
