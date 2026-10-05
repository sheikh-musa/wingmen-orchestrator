#!/usr/bin/env python3
"""ci_bootstrap_schema.py — give CI an ephemeral, migrated Postgres (cp#83 /
backlog#68, held_commitments id=176, bus #46907/#47147).

601 DB-integration tests are currently skipped in CI for lack of a live
database that actually looks like the orchestrator substrate. This brings up
a throwaway local pg17 cluster and replays this repo's full schema chain
against it — schema.sql, then supabase/migrations/*.sql, then
migrations/*.sql, each sorted by filename — so those tests can run against a
realistic schema instead of being skipped.

Empirically discovered (by walking the three layers against a disposable
cluster and recording every "does not exist" error), NOT guessed, two
problems the walk must route around:

  1. A platform-object shim: ~15 roles/schemas/extensions this repo's
     migrations assume a real Supabase project provides (anon/authenticated/
     service_role, auth.role(), pg_cron, pgcrypto, dblink, pgvector) that a
     vanilla local/CI Postgres does not ship. See _apply_platform_shim().

  2. A "bedrock" of 11 tables + 1 view + 2 trigger functions + 1 phantom role
     that are referenced throughout the committed migration chain (GRANT,
     trigger, FK, RLS policy) but have ZERO `CREATE TABLE`/`CREATE FUNCTION`
     anywhere in schema.sql, supabase/migrations/*.sql, or migrations/*.sql —
     confirmed via exhaustive grep. They were created by hand against the
     live substrate before this repo had migration tracking at all.
     migrations/infra/bedrock_substrate_{core,deferred}.sql (captured
     read-only via `pg_dump --schema-only` against the live substrate
     tscuymavysscrvoberrr on 2026-10-05) stand in for them. `core` applies
     before schema.sql; `deferred` applies after the full migrations/*.sql
     walk, because its 9 statements forward-reference objects THIS repo's
     own chain creates later (the console_readonly role from migrations/004,
     mamadah_sources from migrations/012, agents_single_owner_repo_scope()
     from migrations/043).

This is intentionally NOT routed through scripts/apply_migration.py's
ledger/assert/gate pipeline: that pipeline requires every file to carry a
`-- ledger: silo=<ref>` header, but only 32 of this repo's 85 migrations
have one (the convention started partway through the numbering; earlier
files were applied by now-retired one-off apply_*.py scripts before it
existed) — so running the full walk through it would refuse on the other 53
files outright. CI's job here is "build a schema realistic enough for
tests," not "re-prove every historical migration's own production-safety
contract" — there is no ledger, no gate, no residency check, because this
cluster is disposable and never touches a production silo. The one helper
reused from apply_migration.py is strip_txn_control(), to mirror its
dollar-quote-aware BEGIN/COMMIT handling exactly.

Usage:
  python scripts/ci_bootstrap_schema.py up    [--state-file PATH]
  python scripts/ci_bootstrap_schema.py down  [--state-file PATH]

`up` brings up the cluster, applies every layer in order, prints
`DATABASE_URL=...` to stdout, appends the same line to $GITHUB_ENV if set
(so a later CI step's `pytest` sees it), and writes connection/teardown
state to --state-file (default: a fixed path under the OS temp dir — one CI
job runs one bootstrap at a time, so a fixed name is fine). The pg_ctl
server is already a detached background process; `up` does not block.

`down` reads --state-file and stops + cleans up the cluster it describes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_migration as am  # reusing strip_txn_control only — see module docstring

ROOT = Path(__file__).resolve().parent.parent
BEDROCK_CORE = ROOT / "migrations" / "infra" / "bedrock_substrate_core.sql"
BEDROCK_DEFERRED = ROOT / "migrations" / "infra" / "bedrock_substrate_deferred.sql"

DEFAULT_STATE_FILE = Path(tempfile.gettempdir()) / "wingmen-ci-bootstrap-schema-state.json"

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
PG_ENV = {**os.environ, "LC_ALL": "C", "LANG": "C"}

# Never let this script's cluster be mistaken for a real store, however it is
# invoked — same refs tests/conftest.py's assert_dsn_is_not_production() guards.
PRODUCTION_SILO_REFS = (
    "tscuymavysscrvoberrr",
    "ceayjeamtmcyzzvqflus",
    "goumlynecruxrlmzlntp",
    "brrgastulcffamlbggyu",
    "ywrpttpxwfcoodovxhsr",
)

# migrations/004_console_readonly.sql's role password is a psql `-v pw=...`
# bind variable (`:'pw'`), not valid raw SQL for a psycopg cur.execute() call.
# A throwaway literal is all this disposable cluster needs.
_PSQL_BINDVAR_SUBSTITUTIONS = {
    ROOT / "migrations" / "004_console_readonly.sql": {":'pw'": "'ci-ephemeral-throwaway'"},
    # pg_cron isn't installed on this disposable Postgres (unlike pgvector, it needs
    # shared_preload_libraries + a postmaster restart to actually run jobs — not worth
    # it for CI, which never needs a cron job to fire). This file's own comment says
    # the real CREATE EXTENSION call here is only so a LATER file's cron.schedule()
    # works "without a second migration" — the platform shim already provides that
    # (a fake `cron` schema/table/function) independently of the real extension
    # existing, so dropping this one line is a true no-op for everything CI touches.
    # Same file's step 7 ADD CONSTRAINT re-derives agent_messages_message_type_check
    # with its own (older, 7-value) list; bedrock_substrate_core.sql already carries
    # that constraint with the live, evolved 8-value superset (includes a later-added
    # 'counter' this file predates), so it collides by name. Can't whole-file-tolerate
    # this one (DuplicateObject on the LAST statement would roll back steps 1-6's new
    # tables/triggers/view too, in the same implicit multi-statement transaction) —
    # dropping just this one statement is the precise fix.
    ROOT / "supabase" / "migrations" / "20260419_arch035_three_channel_taxonomy.sql": {
        "CREATE EXTENSION IF NOT EXISTS pg_cron;": "-- pg_cron skipped for CI; see _PSQL_BINDVAR_SUBSTITUTIONS comment",
        "ALTER TABLE agent_messages\n  ADD CONSTRAINT agent_messages_message_type_check\n  CHECK (message_type IN ('review_request','question','decision','agreed','challenge','update','blocker'));":
            "-- agent_messages_message_type_check skipped for CI: bedrock_substrate_core.sql already carries the live 8-value superset",
    },
    # Sections 4 & 6 of this 8-section batch ADD 4 columns the cp#83 schema.sql
    # fix-up already carries (posted_by_identity/decided_by_verified/is_test on
    # strategic_decisions, is_test on agent_messages — all confirmed live via the
    # same pg_dump pass). Can't whole-file-tolerate: sections 1-3 (agent_status.
    # base_agent_id), 5 (provenance trigger), 7 (enforcer rewrite) and 8 (the
    # most-evolved boot_briefing, 8 unioned sections) are real, needed objects
    # nothing else in the chain creates — a DuplicateColumn on section 4 would roll
    # back all of those too in the same implicit multi-statement transaction.
    # Dropping just the 4 colliding ADD COLUMN lines is the precise fix.
    ROOT / "supabase" / "migrations" / "20260424_batch1_structural_integrity.sql": {
        "ALTER TABLE strategic_decisions\n  ADD COLUMN posted_by_identity TEXT;":
            "-- posted_by_identity skipped for CI: already on strategic_decisions via schema.sql",
        "ALTER TABLE strategic_decisions\n  ADD COLUMN decided_by_verified BOOLEAN;":
            "-- decided_by_verified skipped for CI: already on strategic_decisions via schema.sql",
        "ALTER TABLE strategic_decisions\n  ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT FALSE;":
            "-- is_test skipped for CI: already on strategic_decisions via schema.sql",
        "ALTER TABLE agent_messages\n  ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT FALSE;":
            "-- is_test skipped for CI: already on agent_messages via bedrock_substrate_core.sql",
    },
    # Section 4's assertion DO block counts actual ROWS returned by boot_briefing's
    # UNION branches (count(DISTINCT source)), not the view's structural branch count.
    # On CI's fresh bootstrap every underlying table (repo_context, strategic_decisions,
    # qa_findings, etc.) is empty, so every branch returns 0 rows and the count is always
    # 0 — failing regardless of whether the view is structurally correct. This is a
    # production-population assumption baked into the migration's own self-check (its
    # header says "qualifies for pre-apply-then-review" — i.e. applied once, by hand, to
    # an already-populated live Supabase project), not a schema gap. Can't whole-file-
    # tolerate: Sections 1-3 (priority_thresholds table + 3 seed rows, inbox_sla_violations
    # view, and the most-evolved 10-branch boot_briefing) are real objects nothing later
    # in the chain re-creates, and a RAISE EXCEPTION here rolls back the whole preceding
    # multi-statement transaction. Neutralizing just the threshold (count can never be
    # negative, so this is an inert no-op) preserves the assertion's structure/intent
    # while accepting that CI genuinely cannot satisfy a live-row-count check.
    ROOT / "supabase" / "migrations" / "20260429_inbox_cadence_section_e.sql": {
        "    -- 10 branches expected; tolerate 9 if a branch has zero rows at apply time\n    IF boot_branches < 8 THEN":
            "    -- cp#83: skipped for CI — a fresh bootstrap has 0 rows in every\n    -- underlying table, so every UNION branch returns 0 rows and this count is\n    -- always 0 regardless of the view's structural correctness (see\n    -- _PSQL_BINDVAR_SUBSTITUTIONS comment in ci_bootstrap_schema.py).\n    IF boot_branches < 0 THEN",
    },
    # Same boot_briefing-row-count assertion shape as the 20260429 entry above (this
    # file's own comment says so: "same fail-loud pattern as ... Phase 1 Section 4") —
    # same cause (0 rows in every underlying table on a fresh CI bootstrap), same fix.
    ROOT / "supabase" / "migrations" / "20260430_paused_jobs_review_branches.sql": {
        "    -- 12 branches now expected: 10 prior + 2 new. Tolerate 10+ in case\n    -- one of the new branches has zero rows at apply time (the source\n    -- text only appears in DISTINCT-source if it has >=1 row).\n    IF branches < 8 THEN":
            "    -- cp#83: skipped for CI — a fresh bootstrap has 0 rows in every\n    -- underlying table, so every UNION branch returns 0 rows regardless of the\n    -- view's structural correctness (see _PSQL_BINDVAR_SUBSTITUTIONS comment\n    -- in ci_bootstrap_schema.py).\n    IF branches < 0 THEN",
    },
    # lane_tasks_state's CREATE VIEW (lines 63/73/74 of this file) selects
    # t.started_at/t.sla_minutes/t.sla_breached_at, but migrations/008_lane_tasks.sql's
    # CREATE TABLE never had them and no migration in any of the 3 committed layers
    # (confirmed by grep) ever ADDs them either — hand-added to the live substrate
    # before migration tracking existed, same phantom class as a bedrock table, just a
    # column instead of a whole relation. Can't live in bedrock_substrate_core.sql:
    # that runs before schema.sql/migrations/008 even create lane_tasks. Can't defer to
    # bedrock_substrate_deferred.sql either: that runs after the ENTIRE migrations/*.sql
    # walk, and this file's own CREATE VIEW needs the columns mid-walk, before deferred
    # ever runs. Injecting the 3 missing ADD COLUMNs immediately ahead of this file's
    # own 4 (which both ADD COLUMN IF NOT EXISTS, so re-apply stays idempotent) is the
    # only point in the walk where the table exists yet the view hasn't been created.
    ROOT / "migrations" / "048_lane_task_acceptance.sql": {
        "ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS acceptance_criteria text;":
            "-- cp#83: started_at/sla_minutes/sla_breached_at are hand-added live columns\n"
            "-- with zero CREATE/ADD COLUMN anywhere in schema.sql/supabase/migrations/migrations\n"
            "-- (confirmed by grep) -- this view's own SELECT needs them to exist first.\n"
            "ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS started_at timestamptz;\n"
            "ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS sla_minutes integer;\n"
            "ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS sla_breached_at timestamptz;\n"
            "ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS acceptance_criteria text;",
    },
    # This file's own header already documents both DROPped functions as untracked-
    # by-any-migration security remediation targets ("No tracked migration ever
    # created it... zero references anywhere in this repo") — confirmed via a
    # pg_proc count query that both are ALSO absent from the live substrate now
    # (migration 058 already ran there; the DROP is permanent), same phantom-and-
    # dead class as migrations/011's post_journal_atomic. Can't whole-file-tolerate
    # via _DEAD_MIGRATION_TARGETS though: that skips the WHOLE file's exec() call,
    # including the 8 ALTER FUNCTION...SET search_path statements below the 2 DROPs
    # — those targets are real and 6 of the 8 are themselves undiscovered bedrock
    # phantoms now captured in bedrock_substrate_core.sql (auth_user_org_ids() etc.),
    # so this walk needs those ALTERs to actually run. Dropping just the 2 DROP
    # FUNCTION statements (same "precise fix" pattern as migrations/004 above) is
    # correct here.
    ROOT / "migrations" / "058_close_anon_exec_postgres_owned_half.sql": {
        "DROP FUNCTION public.fetch_and_execute_sql(text);":
            "-- cp#83: fetch_and_execute_sql skipped for CI -- confirmed dead on both\n"
            "-- the committed chain and the live substrate (already DROPped there).",
        "DROP FUNCTION public.load_all_morphology_batches(text, integer, integer);":
            "-- cp#83: load_all_morphology_batches skipped for CI -- same dead class\n"
            "-- as fetch_and_execute_sql, which its body called.",
    },
    # agent_messages_reject_pseudo_targets already lands via bedrock_substrate_core.sql
    # (captured as part of agent_messages' current live full definition, same
    # schema.sql-is-a-cumulative-snapshot overlap class as the _KNOWN_SCHEMA_SQL_OVERLAP
    # entries above, just sourced from the bedrock capture instead of schema.sql). Can't
    # whole-file-tolerate: the DO $$ self-check below the ADD CONSTRAINT is the actual
    # point of this migration (asserts the constraint exists AND is NOT VALID) and must
    # still run -- it passes on its own against the bedrock-provided constraint, which
    # is already in the exact NOT VALID shape this migration asserts. Dropping just the
    # duplicate ADD CONSTRAINT statement is the precise fix.
    ROOT / "migrations" / "060_agent_messages_reject_pseudo_targets.sql": {
        "ALTER TABLE agent_messages\n  ADD CONSTRAINT agent_messages_reject_pseudo_targets\n  CHECK (to_agent <> 'substrate') NOT VALID;":
            "-- cp#83: skipped for CI -- already carried by bedrock_substrate_core.sql\n"
            "-- (agent_messages' live definition already includes this constraint).",
    },
    # This migration's precondition gate hardcodes the exact 5 historical row ids
    # (1675/1758/16347/16348/16352) it was written against -- deliberately strict
    # for the real apply (abort if the bad-row set drifted since authoring). A fresh
    # CI bootstrap has 0 rows in operator_messages, so array_agg returns NULL, which
    # IS DISTINCT FROM the expected non-null array -- same "0 rows everywhere" shape
    # as the boot_briefing row-count assertions above, not real drift. Tolerating
    # bad_ids IS NULL alongside the exact-match case preserves the real precondition
    # (still aborts on any OTHER non-matching non-null set) while letting a fresh
    # database pass.
    ROOT / "migrations" / "080_operator_messages_tag_shape.sql": {
        "IF bad_ids IS DISTINCT FROM ARRAY[1675, 1758, 16347, 16348, 16352]::bigint[] THEN":
            "-- cp#83: bad_ids IS NULL tolerated for CI -- a fresh bootstrap has 0 rows\n"
            "  -- in operator_messages, so array_agg returns NULL (not real drift).\n"
            "  IF bad_ids IS DISTINCT FROM ARRAY[1675, 1758, 16347, 16348, 16352]::bigint[] AND bad_ids IS NOT NULL THEN",
    },
    # Same "0 rows on a fresh CI bootstrap, not real drift" class as 080 above: this
    # migration hardcodes 8 specific historical held_commitments ids (3/4/7/8/12/13/
    # 15/16) that don't exist on an empty table. The UPDATE/round-trip/remaining-
    # violations asserts below all pass vacuously on 0 rows -- only the snapshot-count
    # precondition needs to tolerate 0 alongside the real-apply expectation of 8.
    ROOT / "migrations" / "090_held_commitments_payload_json_check.sql": {
        "if v_snapshot_count <> 8 then":
            "-- cp#83: 0 tolerated for CI -- a fresh bootstrap has 0 rows in\n"
            "  -- held_commitments, so none of the 8 historical ids exist (not real drift).\n"
            "  if v_snapshot_count <> 8 and v_snapshot_count <> 0 then",
    },
}

# schema.sql is a cumulative snapshot — some commits (e.g. 6963f27, [TASK-032])
# updated BOTH schema.sql (the fresh-install doc) and a supabase/migrations file
# (the apply-to-an-existing-live-project path) with the same end-state change, since
# each file only ever runs on its own in real use. Chaining both in one walk, as this
# script does, is the first thing to ever hit that overlap: the migration's
# `ADD CONSTRAINT <auto-generated-name>` collides with the identically-named
# constraint schema.sql's own inline `column ... check (...)` already created.
# Verified case-by-case before listing here — not a blind ignore-all. A fresh CI
# database has 0 rows, so the UPDATE/backfill statements these files also carry are
# no-ops regardless; skipping the file changes nothing else.
_KNOWN_SCHEMA_SQL_OVERLAP = {
    # strategic_decisions.category + its CHECK already land via schema.sql line
    # ~295-296 (same commit); this file's ADD CONSTRAINT re-derives the identical
    # auto-generated name `strategic_decisions_category_check` and collides.
    # Safe as a whole-file tolerate because the rest of that file is empty-table
    # UPDATE backfills — no-ops either way on a fresh CI database.
    ROOT / "supabase" / "migrations" / "20260416_task032_category_parent_ref.sql": "DuplicateObject",
    # Fails on its FIRST statement (ADD COLUMN priority — bedrock_substrate_core.sql's
    # pg_dump of the live table already has it, default 'P2' + the same CHECK), so the
    # whole batch (incl. the trailing ADD CONSTRAINT + CREATE INDEX) never runs. Unlike
    # arch035 above, that's correct here, not just harmless: bedrock's priority column
    # AND idx_agent_messages_open_by_priority index already match live exactly, and the
    # extra agent_messages_priority_requires_response_check this file would add is
    # ABSENT from today's pg_dump — it was live once but isn't now (dropped by something
    # later, not captured in this file). Re-adding it in CI would make CI reject P0/P1
    # messages with requires_response=false that production currently accepts — less
    # faithful, not more. Skipping the whole file matches live reality on all 3 counts.
    ROOT / "supabase" / "migrations" / "20260420_arch036_priority_column.sql": "DuplicateColumn",
}

# These are NOT schema.sql overlaps — each is a migration whose target was confirmed,
# via pg_get_functiondef against the live orchestrator substrate (2026-10-05), to not
# exist there EITHER, and has zero CREATE anywhere in any of the 3 committed layers.
# Unlike every entry above (CI-only artifacts of an empty database), this is a genuine
# defect in the committed migration chain: replaying it from scratch — e.g. a real
# disaster-recovery rebuild of the orchestrator substrate, which is exactly what this
# CI bootstrap simulates — would ALSO fail here, independent of CI. Flagged separately
# in the cp#83 report as a standing issue for cc-orchestrator/cai, not silently patched
# over. Safe to whole-file-tolerate only because each listed file's statements all
# target the same nonexistent object and nothing else in any layer ever references it
# (verified via grep) — skipping changes nothing else, structurally or semantically.
_DEAD_MIGRATION_TARGETS = {
    # Both statements (REVOKE + GRANT) target public.post_journal_atomic(jsonb, bigint,
    # uuid) — confirmed absent from the live substrate's pg_proc (only a differently-named
    # enforce_balanced_journal exists) and absent from every CREATE FUNCTION in schema.sql/
    # supabase/migrations/migrations. The file's own comment ("049 ledger, substrate")
    # doesn't resolve to any migration in this repo that creates it either. Likely a
    # function created by hand, granted/revoked via this file, then dropped again without
    # ever being captured in a migration — this file is now a dead historical artifact.
    ROOT / "migrations" / "011_substrate_post_journal_lockdown.sql": "UndefinedFunction",
}

PLATFORM_SHIM_SQL = """
-- Platform-object shim (cc-orchestrator ask, bus #47147): standard Supabase
-- roles/schemas/extensions this repo's migrations assume exist, which a
-- vanilla local/CI Postgres does not ship. Each block says what it stands in
-- for and why this repo's own chain needs it.

-- anon / authenticated / service_role: Supabase's three standard PostgREST
-- roles, referenced throughout schema.sql/migrations in GRANT and
-- `CREATE POLICY ... TO <role>` clauses. NOLOGIN stand-ins are enough —
-- nothing in this repo's test suite authenticates AS them, it only needs
-- them to EXIST so those statements don't fail with "role does not exist".
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN;
  END IF;
  -- cto_desktop: NOT a real Supabase role — a phantom application role
  -- granted two RLS policies on agent_messages (see
  -- migrations/infra/bedrock_substrate_core.sql) with zero CREATE ROLE
  -- anywhere in the committed chain. Created by hand against the live
  -- substrate before migration tracking existed.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cto_desktop') THEN
    CREATE ROLE cto_desktop NOLOGIN;
  END IF;
END
$$;

-- auth schema + auth.role()/auth.jwt(): Postgres resolves the function name
-- inside `CREATE POLICY ... USING (auth.role() = 'service_role')` at CREATE
-- POLICY DDL time, not call time (unlike a table reference inside a function
-- body, which only resolves when the function is later called) — so even a
-- policy this repo's own tests never evaluate still needs the function to
-- exist at schema-build time. auth.jwt() was added here after cp#83's mizan_*
-- bedrock tables (migrations/infra/bedrock_substrate_core.sql) surfaced
-- `auth.jwt() ->> 'role'` / `auth.jwt() ->> 'telegram_id_hash'` policies —
-- the prior assumption that only OTHER products' views use auth.jwt(), never
-- this repo's own committed chain, turned out to be false. auth.uid() was
-- added the same way after cp#83's auth_user_org_ids()/auth_user_org_ids_with_role()/
-- auth_user_org_ids_with_roles()/auth_user_hr_employee_id() bedrock functions
-- (LANGUAGE sql, so their body IS parsed/planned at CREATE FUNCTION time, unlike
-- a plpgsql body) surfaced a direct `auth.uid()` call each — same "only OTHER
-- products use it" assumption, same falsification.
CREATE SCHEMA IF NOT EXISTS auth;
CREATE OR REPLACE FUNCTION auth.role() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT 'service_role'::text $$;
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb
LANGUAGE sql STABLE AS $$ SELECT '{}'::jsonb $$;
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$;

-- auth.users: public.profiles.id FKs to it (ON DELETE CASCADE) -- profiles
-- is itself a phantom table (cp#83, migration 013's RLS lockdown references
-- it transitively via organizations/anchor_batches) being backfilled into
-- bedrock_substrate_core.sql, not something this repo's chain creates. A
-- bare id column is enough to satisfy the FK; nothing in the walk queries
-- auth.users' other real columns.
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY
);

-- cron schema + cron.job + cron.schedule(): stands in for the pg_cron
-- extension (platform-only, not installable on vanilla Postgres).
-- supabase/migrations/20260420_governance_hygiene_batch.sql calls
-- cron.schedule(jobname, schedule, command) to register two scheduled
-- jobs — the only call shape used anywhere in this repo's chain (confirmed
-- by grep); it just needs to succeed as a no-op here, since nothing in this
-- repo's test suite exercises pg_cron's actual scheduler.
CREATE SCHEMA IF NOT EXISTS cron;
CREATE TABLE IF NOT EXISTS cron.job (
    jobid bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jobname text,
    schedule text,
    command text
);
CREATE OR REPLACE FUNCTION cron.schedule(jobname text, schedule text, command text) RETURNS bigint
LANGUAGE sql AS $$
    INSERT INTO cron.job (jobname, schedule, command) VALUES (jobname, schedule, command)
    RETURNING jobid
$$;

-- supabase_migrations.schema_migrations: the Supabase CLI's own bookkeeping
-- table. Convention 3 (docs/substrate/migration-conventions.md) populates it
-- directly via `INSERT ... ON CONFLICT (version) DO NOTHING`, which needs a
-- PK on version — added here (the live substrate's real shape, captured via
-- pg_dump on 2026-10-05, has the same columns but no such PK; every
-- ON CONFLICT (version) caller in this repo's chain assumes one anyway).
CREATE SCHEMA IF NOT EXISTS supabase_migrations;
CREATE TABLE IF NOT EXISTS supabase_migrations.schema_migrations (
    version text PRIMARY KEY,
    statements text[],
    name text,
    created_by text,
    idempotency_key text,
    rollback text[]
);

-- pgcrypto / dblink: genuinely used by this repo's own committed chain
-- (gen_random_bytes/digest, dblink_connect/dblink_exec) — real extensions,
-- not shims, installable via the standard Postgres contrib package.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS dblink;

-- vector (pgvector): genuinely used by mamadah_notes.embedding
-- (public.vector(1024)) — a real extension, not a shim. Installed via
-- Homebrew locally (`brew install pgvector`) / the postgresql-17-pgvector
-- apt package in CI (see .github/workflows/ci.yml).
CREATE EXTENSION IF NOT EXISTS vector;
""".strip()


def _pg_bin(name: str) -> str:
    return os.path.join(PG_BIN, name)


def _free_port() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


def _assert_not_production(dsn: str) -> None:
    for ref in PRODUCTION_SILO_REFS:
        if ref in dsn:
            raise RuntimeError(f"refusing: DSN contains a PRODUCTION silo ref {ref!r} — {dsn!r}")


def bring_up_cluster() -> tuple[str, str, str, str]:
    """Returns (dsn, datadir, sockdir, port). Session-lifetime, not torn down
    per-test (unlike tests/conftest.py's pg_dsn fixture) — this cluster is
    meant to survive an entire CI job."""
    datadir = tempfile.mkdtemp(prefix="wingmen-ci-pg-")
    shutil.rmtree(datadir)  # initdb wants to create it
    sockdir = tempfile.mkdtemp(prefix="wingmen-ci-pg-sock-")
    port = _free_port()
    subprocess.run(
        [_pg_bin("initdb"), "-D", datadir, "-U", "postgres",
         "--auth=trust", "--locale=C", "--encoding=UTF8"],
        check=True, capture_output=True, env=PG_ENV,
    )
    subprocess.run(
        [_pg_bin("pg_ctl"), "-D", datadir, "-l", os.path.join(datadir, "log"),
         "-o", f"-p {port} -k {sockdir} -c listen_addresses='' "
               f"-c timezone=UTC -c log_timezone=UTC",
         "-w", "start"],
        check=True, capture_output=True, env=PG_ENV,
    )
    dsn = f"host={sockdir} port={port} user=postgres dbname=postgres"
    _assert_not_production(dsn)
    for _ in range(100):
        try:
            with psycopg.connect(dsn):
                break
        except psycopg.OperationalError:
            time.sleep(0.1)
    else:
        raise RuntimeError("pg17 did not accept connections within 10s of pg_ctl start")
    return dsn, datadir, sockdir, port


def tear_down_cluster(datadir: str, sockdir: str) -> None:
    subprocess.run([_pg_bin("pg_ctl"), "-D", datadir, "-w", "stop"],
                   capture_output=True, env=PG_ENV)
    shutil.rmtree(datadir, ignore_errors=True)
    shutil.rmtree(sockdir, ignore_errors=True)


def _apply_sql(dsn: str, sql_text: str, label: str, *, tolerate: str | None = None) -> None:
    body = am.strip_txn_control(sql_text)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(body)
    except Exception as e:
        if tolerate and type(e).__name__ == tolerate:
            print(f"  (tolerating known {tolerate} in {label}: {e})", file=sys.stderr)
            return
        raise RuntimeError(f"failed applying {label}: {e}") from e


def _apply_file(dsn: str, path: Path) -> None:
    sql_text = path.read_text()
    for needle, replacement in _PSQL_BINDVAR_SUBSTITUTIONS.get(path, {}).items():
        sql_text = sql_text.replace(needle, replacement)
    tolerate = _KNOWN_SCHEMA_SQL_OVERLAP.get(path) or _DEAD_MIGRATION_TARGETS.get(path)
    _apply_sql(dsn, sql_text, str(path.relative_to(ROOT)), tolerate=tolerate)


def apply_platform_shim(dsn: str) -> None:
    _apply_sql(dsn, PLATFORM_SHIM_SQL, "platform-object shim")


def apply_full_schema(dsn: str, *, verbose: bool = True) -> None:
    def log(msg: str) -> None:
        if verbose:
            print(msg, file=sys.stderr)

    log("applying platform-object shim")
    apply_platform_shim(dsn)

    log(f"applying {BEDROCK_CORE.relative_to(ROOT)}")
    _apply_file(dsn, BEDROCK_CORE)

    log("applying schema.sql")
    _apply_file(dsn, ROOT / "schema.sql")

    supa_files = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
    log(f"applying supabase/migrations/*.sql ({len(supa_files)} files)")
    for f in supa_files:
        log(f"  {f.name}")
        _apply_file(dsn, f)

    mig_files = sorted((ROOT / "migrations").glob("*.sql"))
    log(f"applying migrations/*.sql ({len(mig_files)} files)")
    for f in mig_files:
        log(f"  {f.name}")
        _apply_file(dsn, f)

    log(f"applying {BEDROCK_DEFERRED.relative_to(ROOT)}")
    _apply_file(dsn, BEDROCK_DEFERRED)

    log("schema bootstrap complete")


def cmd_up(state_file: Path) -> int:
    dsn, datadir, sockdir, port = bring_up_cluster()
    try:
        apply_full_schema(dsn)
    except Exception:
        tear_down_cluster(datadir, sockdir)
        raise

    state_file.write_text(
        __import__("json").dumps(
            {"dsn": dsn, "datadir": datadir, "sockdir": sockdir, "port": port}
        )
    )
    print(f"DATABASE_URL={dsn}")
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a") as f:
            f.write(f"DATABASE_URL={dsn}\n")
    return 0


def cmd_down(state_file: Path) -> int:
    if not state_file.exists():
        print(f"no state file at {state_file} — nothing to tear down", file=sys.stderr)
        return 0
    state = __import__("json").loads(state_file.read_text())
    tear_down_cluster(state["datadir"], state["sockdir"])
    state_file.unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["up", "down"])
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    args = parser.parse_args(argv)

    if args.action == "up":
        return cmd_up(args.state_file)
    return cmd_down(args.state_file)


if __name__ == "__main__":
    raise SystemExit(main())
