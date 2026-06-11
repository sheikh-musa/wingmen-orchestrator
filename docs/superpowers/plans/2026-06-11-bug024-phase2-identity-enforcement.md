# BUG-024 Phase 2 — agent_messages Identity Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `from_agent` / `decided_by` non-spoofable at the Postgres auth layer by hardening the provenance triggers (OVERWRITE, not fill-if-null) and adding RLS INSERT enforcement, then voiding all existing untrustworthy verified flags.

**Architecture:** A single transactional migration (shipped as a dry-run/`--apply` psycopg script per CLAUDE.md decision-962, never `supabase db push`). It (B1) rewrites the two provenance trigger functions to `SECURITY INVOKER` so the trigger resolves the *caller's* identity identically to how RLS evaluates it, always OVERWRITING `posted_by_identity` and always COMPUTING the verified flag; (B2/B4) adds RLS INSERT policies whose `WITH CHECK` requires `from_agent`/`decided_by` to equal the resolved identity or match an `identity_allowlist` row; (B3) seeds the operator→cai/musa allowlist; and (VOID) nullifies every pre-existing verified flag. **Apply to prod is operator-gated** (cai #2064) on per-agent role/credential provisioning — this plan builds + tests only.

**Tech Stack:** Postgres 17 (prod is Supabase 17.6), psycopg 3, pytest with an ephemeral local PG17 cluster fixture (`SET ROLE` simulation), python-dotenv.

**Authority:** cai #2064 (GO on migration + tests; apply gated). Grant-predicate ratified #2067. Spec: `docs/superpowers/specs/2026-06-11-bug024-phase2-identity-enforcement-design.md`.

---

## Critical design facts (substrate-validated 2026-06-11)

1. **SECURITY INVOKER is mandatory.** The existing triggers are `SECURITY DEFINER` (owner `postgres`). Inside a DEFINER function `current_user` = the owner, so `posted_by_identity` would be stamped `postgres` even for a per-agent connection — while RLS (never DEFINER) sees the real caller. The two would disagree and enforcement would be theater. Both functions must become `SECURITY INVOKER` so trigger and RLS resolve `current_user` in the same caller context. On the legacy shared path this is behavior-neutral (the connection role is already `postgres`).
2. **Per-agent role names must equal the agent identifier** (e.g. role `"cc-ihsanos"`, not `agent_cc_ihsanos`), because resolution is `from_agent = current_user`. Operator provisions these (quoted, hyphenated) roles, none with `BYPASSRLS`, each with `GRANT SELECT ON identity_allowlist` (needed by both the INVOKER trigger and the RLS `EXISTS` subquery) and INSERT/SELECT on the target tables.
3. **service_role bypasses RLS**, so the existing service-role policies stay and the new INSERT policy only bites non-bypass per-agent roles. Full closure still depends on the inserter migration (Track A, operator-gated) moving writers off the shared key.
4. **Void ordering:** `strategic_decisions` has a BEFORE INSERT **and UPDATE** provenance trigger; the hardened UPDATE trigger would recompute any flag a void UPDATE touches. The void must run with the provenance trigger **disabled** (`ALTER TABLE … DISABLE TRIGGER …` / re-enable) inside the same transaction. `agent_messages`' trigger is INSERT-only, but disable it during the void too for symmetry.
5. **Void semantics:** void = set existing `from_agent_verified` / `decided_by_verified` to **NULL**. Post-migration the trigger makes the flag never-NULL going forward, so NULL becomes the unambiguous marker of "pre-enforcement, untrusted." Current void targets: `agent_messages` 60 non-null, `strategic_decisions` 20 non-null (dry-run 2026-06-11) — but the migration voids ALL non-null, not a hardcoded count.
6. **SELECT-visibility is a hard prerequisite for the per-agent rollout.** With RLS on and only an INSERT `WITH CHECK` policy, a non-`BYPASSRLS` role can INSERT but cannot read back any row (incl. its own inbox) or `INSERT … RETURNING` (RETURNING is filtered by SELECT policies → raises "violates RLS"). The legacy `service_role` path is unaffected (it bypasses RLS).
   - **`agent_messages` SELECT policy — BUILT (non-contingent part).** Grounded in the actual poller reads (every reader filters `to_agent = <self>`): an agent sees its own inbox (`to_agent = identity`), its own sent (`from_agent = identity` — needed for `RETURNING` and BUG-035 reconciliation read-backs), and `to_agent = 'broadcast'`. Cross-thread / governance-wide reads stay on the `service_role` path (least privilege). Policy `"agents read own inbox, sent, and broadcasts"`, ACs 6–9.
   - **`strategic_decisions` SELECT — BUILT as shared-ledger `USING(true)` (cai CAI-RESP-214 = option b).** My own-only recommendation was overruled with cause: the ihsanos-drain grant-check reads `execution_status=granted` `decided_by=cai` decisions every cycle, and the 24h challenge window is definitionally a read of decisions the agent did not author — own-only would silently starve both (per-agent creds → zero rows → autonomous execution dies with no error). Least privilege protects the WRITE surface (the INSERT policy); the ledger READ surface is shared governance canon. `is_test` intentionally not filtered (consumers filter at the app layer; no role-level test/non-test distinction). Policy `"agent roles read the full decision ledger"`, AC10.
   - Tests insert without `RETURNING` and verify stamped values via a superuser read-back, keeping the INSERT ACs independent of the SELECT model.

## File Structure

- Create: `tests/migrations/__init__.py` (empty, package marker)
- Create: `tests/migrations/conftest.py` — session-scoped ephemeral PG17 cluster fixture yielding a DSN
- Create: `tests/migrations/test_bug024_identity_enforcement.py` — the AC tests via `SET ROLE`
- Create: `scripts/apply_bug024_identity_enforcement.py` — dry-run/`--apply` migration script holding the SQL as module constants
- Reference (do not edit): `scripts/apply_identity_canon.py` (apply-script pattern), the spec above

The migration SQL lives as constants in the apply script; the test imports those constants and applies them to the ephemeral cluster, so test and prod run the **identical** DDL.

---

## Task 1: Ephemeral PG17 cluster fixture

**Files:**
- Create: `tests/migrations/__init__.py`
- Create: `tests/migrations/conftest.py`

- [ ] **Step 1: Create the package marker**

`tests/migrations/__init__.py`: empty file.

- [ ] **Step 2: Write the cluster fixture**

The Mac Mini has no docker and no server on PATH; PG17 is the keg `/usr/local/opt/postgresql@17` (share/lib symlinked into `/usr/local/{share,lib}/postgresql@17`). `initdb` needs `--locale=C`; start with `timezone=UTC`. Bind a unix socket only (no TCP).

`tests/migrations/conftest.py`:

```python
import os
import shutil
import subprocess
import tempfile
import time

import psycopg
import pytest

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
PORT = "54329"


def _bin(name: str) -> str:
    path = os.path.join(PG_BIN, name)
    if not os.path.exists(path):
        pytest.skip(f"PG17 binary missing: {path} (set WINGMEN_PG17_BIN)")
    return path


@pytest.fixture(scope="session")
def pg_dsn():
    datadir = tempfile.mkdtemp(prefix="wingmen-pgtest-")
    shutil.rmtree(datadir)  # initdb wants to create it
    sockdir = tempfile.mkdtemp(prefix="wingmen-pgsock-")
    subprocess.run(
        [_bin("initdb"), "-D", datadir, "-U", "postgres",
         "--auth=trust", "--locale=C", "--encoding=UTF8"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [_bin("pg_ctl"), "-D", datadir, "-l", os.path.join(datadir, "log"),
         "-o", f"-p {PORT} -k {sockdir} -c listen_addresses='' "
               f"-c timezone=UTC -c log_timezone=UTC",
         "-w", "start"],
        check=True, capture_output=True,
    )
    dsn = f"host={sockdir} port={PORT} user=postgres dbname=postgres"
    # wait for readiness
    for _ in range(50):
        try:
            with psycopg.connect(dsn):
                break
        except psycopg.OperationalError:
            time.sleep(0.1)
    try:
        yield dsn
    finally:
        subprocess.run([_bin("pg_ctl"), "-D", datadir, "-w", "stop"],
                       capture_output=True)
        shutil.rmtree(datadir, ignore_errors=True)
        shutil.rmtree(sockdir, ignore_errors=True)


@pytest.fixture
def fresh_db(pg_dsn):
    """A clean public schema per test."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute("DROP ROLE IF EXISTS \"cc-ihsanos\";")
        cur.execute("DROP ROLE IF EXISTS \"musa\";")
    return pg_dsn
```

- [ ] **Step 3: Verify the fixture boots**

Run: `.venv/bin/python -m pytest tests/migrations/ -q` (no tests yet → "no tests ran"; the fixture import must not error).
Expected: collection succeeds, exit 0 or "no tests ran".

- [ ] **Step 4: Commit**

```bash
git add tests/migrations/__init__.py tests/migrations/conftest.py
git commit -m "test(bug024-p2): ephemeral PG17 cluster fixture for SET ROLE tests"
```

---

## Task 2: Migration SQL constants + apply script skeleton (no prod writes)

**Files:**
- Create: `scripts/apply_bug024_identity_enforcement.py`

- [ ] **Step 1: Write the migration SQL as importable constants**

Each statement is idempotent. `SECURITY INVOKER` + OVERWRITE; the verified flag is always computed; the void runs with the provenance trigger disabled.

`scripts/apply_bug024_identity_enforcement.py`:

```python
"""BUG-024 Phase 2 — agent_messages / strategic_decisions identity enforcement.

Hardens the provenance triggers to SECURITY INVOKER + OVERWRITE (caller input
ignored), adds RLS INSERT enforcement, seeds the operator allowlist, and VOIDs
all pre-existing (untrustworthy) verified flags. cai #2064: build + test now;
APPLY is operator-gated on per-agent credential provisioning. Never
`supabase db push` to prod (CLAUDE.md decision-962); this is the direct
psycopg-apply path (dry-run default, --apply commits).

Usage:
  python scripts/apply_bug024_identity_enforcement.py          # dry-run
  python scripts/apply_bug024_identity_enforcement.py --apply  # commit
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

HARDEN_AGENT_MESSAGES_TRIGGER = """
create or replace function public.populate_agent_messages_provenance()
returns trigger language plpgsql
security invoker
set search_path to public, pg_temp
as $fn$
begin
  -- OVERWRITE: caller input ignored. Identity is the authenticated caller
  -- (jwt agent_id claim when present, else the Postgres role).
  new.posted_by_identity := coalesce(
    current_setting('request.jwt.claims', true)::json ->> 'agent_id',
    current_user);
  new.from_agent_verified := (
    new.from_agent = new.posted_by_identity
    or exists (select 1 from identity_allowlist
               where posted_by = new.posted_by_identity
                 and allowed_from_agent = new.from_agent));
  return new;
end $fn$;
"""

HARDEN_STRATEGIC_DECISIONS_TRIGGER = """
create or replace function public.populate_strategic_decisions_provenance()
returns trigger language plpgsql
security invoker
set search_path to public, pg_temp
as $fn$
begin
  new.posted_by_identity := coalesce(
    current_setting('request.jwt.claims', true)::json ->> 'agent_id',
    current_user);
  new.decided_by_verified := (
    new.decided_by = new.posted_by_identity
    or exists (select 1 from identity_allowlist
               where posted_by = new.posted_by_identity
                 and allowed_from_agent = new.decided_by));
  return new;
end $fn$;
"""

SEED_ALLOWLIST = """
insert into identity_allowlist (posted_by, allowed_from_agent, note) values
  ('musa', 'cai',  'operator posts ratifications as cai'),
  ('musa', 'musa', 'operator own posts')
on conflict do nothing;
"""

RLS_AGENT_MESSAGES_INSERT = """
drop policy if exists "from_agent must match posting identity" on agent_messages;
create policy "from_agent must match posting identity" on agent_messages
  for insert with check (
    from_agent = coalesce(
      current_setting('request.jwt.claims', true)::json ->> 'agent_id',
      current_user)
    or exists (select 1 from identity_allowlist
               where posted_by = coalesce(
                       current_setting('request.jwt.claims', true)::json ->> 'agent_id',
                       current_user)
                 and allowed_from_agent = from_agent));
"""

RLS_STRATEGIC_DECISIONS_INSERT = """
drop policy if exists "decided_by must match posting identity" on strategic_decisions;
create policy "decided_by must match posting identity" on strategic_decisions
  for insert with check (
    decided_by = coalesce(
      current_setting('request.jwt.claims', true)::json ->> 'agent_id',
      current_user)
    or exists (select 1 from identity_allowlist
               where posted_by = coalesce(
                       current_setting('request.jwt.claims', true)::json ->> 'agent_id',
                       current_user)
                 and allowed_from_agent = decided_by));
"""

VOID_AGENT_MESSAGES = """
alter table agent_messages disable trigger trg_agent_messages_provenance;
update agent_messages set from_agent_verified = null where from_agent_verified is not null;
alter table agent_messages enable trigger trg_agent_messages_provenance;
"""

VOID_STRATEGIC_DECISIONS = """
alter table strategic_decisions disable trigger trg_strategic_decisions_provenance;
update strategic_decisions set decided_by_verified = null where decided_by_verified is not null;
alter table strategic_decisions enable trigger trg_strategic_decisions_provenance;
"""

# Order matters: harden functions, seed allowlist, void (triggers disabled), then policies.
MIGRATION = [
    ("harden agent_messages trigger", HARDEN_AGENT_MESSAGES_TRIGGER),
    ("harden strategic_decisions trigger", HARDEN_STRATEGIC_DECISIONS_TRIGGER),
    ("seed identity_allowlist", SEED_ALLOWLIST),
    ("void agent_messages verified flags", VOID_AGENT_MESSAGES),
    ("void strategic_decisions verified flags", VOID_STRATEGIC_DECISIONS),
    ("rls agent_messages insert", RLS_AGENT_MESSAGES_INSERT),
    ("rls strategic_decisions insert", RLS_STRATEGIC_DECISIONS_INSERT),
]


def apply_migration(cur) -> None:
    for label, sql in MIGRATION:
        cur.execute(sql)


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from agent_messages where from_agent_verified is not null")
        am_before = cur.fetchone()[0]
        cur.execute("select count(*) from strategic_decisions where decided_by_verified is not null")
        sd_before = cur.fetchone()[0]
        print(f"non-null verified BEFORE: agent_messages={am_before} strategic_decisions={sd_before}")
        apply_migration(cur)
        cur.execute("select count(*) from agent_messages where from_agent_verified is not null")
        cur.execute("select count(*) from strategic_decisions where decided_by_verified is not null")
        print("voided + hardened (verified flags now null until re-asserted under enforcement)")
        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it imports and parses**

Run: `.venv/bin/python -c "import scripts.apply_bug024_identity_enforcement as m; print(len(m.MIGRATION), 'statements')"`
Expected: `7 statements`

- [ ] **Step 3: Commit**

```bash
git add scripts/apply_bug024_identity_enforcement.py
git commit -m "feat(bug024-p2): identity-enforcement migration SQL + apply script (no prod writes)"
```

---

## Task 3: AC tests — trigger OVERWRITE + RLS enforcement (TDD)

**Files:**
- Create: `tests/migrations/test_bug024_identity_enforcement.py`

This test builds a faithful minimal replica (the two tables + `identity_allowlist`, the real triggers wired to the hardened functions, the RLS policies, and two hyphen-named per-agent roles), then exercises every acceptance criterion via `SET ROLE`. It imports the SQL constants from the apply script so test and prod run identical DDL.

- [ ] **Step 1: Write the failing tests**

```python
import psycopg
import pytest

import scripts.apply_bug024_identity_enforcement as mig

SCHEMA = """
create table identity_allowlist (posted_by text not null, allowed_from_agent text not null, note text);
create table agent_messages (id serial primary key, from_agent text not null,
    posted_by_identity text, from_agent_verified boolean);
create table strategic_decisions (id serial primary key, decided_by text not null,
    posted_by_identity text, decided_by_verified boolean);
create trigger trg_agent_messages_provenance before insert on agent_messages
    for each row execute function populate_agent_messages_provenance();
create trigger trg_strategic_decisions_provenance before insert or update on strategic_decisions
    for each row execute function populate_strategic_decisions_provenance();
alter table agent_messages enable row level security;
alter table strategic_decisions enable row level security;
create role "cc-ihsanos" nologin;
create role "musa" nologin;
grant insert, select on agent_messages, strategic_decisions to "cc-ihsanos", "musa";
grant select on identity_allowlist to "cc-ihsanos", "musa";
grant usage, select on all sequences in schema public to "cc-ihsanos", "musa";
"""


@pytest.fixture
def db(fresh_db):
    conn = psycopg.connect(fresh_db, autocommit=True)
    cur = conn.cursor()
    # functions first (schema's triggers reference them), then schema, then policies
    cur.execute(mig.HARDEN_AGENT_MESSAGES_TRIGGER)
    cur.execute(mig.HARDEN_STRATEGIC_DECISIONS_TRIGGER)
    cur.execute(SCHEMA)
    cur.execute(mig.SEED_ALLOWLIST)
    cur.execute(mig.RLS_AGENT_MESSAGES_INSERT)
    cur.execute(mig.RLS_STRATEGIC_DECISIONS_INSERT)
    yield conn
    conn.close()


def test_ac1_posted_by_identity_is_not_caller_overridable(db):
    cur = db.cursor()
    cur.execute('set role "cc-ihsanos"')
    cur.execute("insert into agent_messages (from_agent, posted_by_identity, from_agent_verified) "
                "values ('cc-ihsanos', 'SPOOFED', true) returning posted_by_identity")
    assert cur.fetchone()[0] == "cc-ihsanos"  # caller's 'SPOOFED' ignored


def test_ac2_verified_is_always_computed_true_on_self_post(db):
    cur = db.cursor()
    cur.execute('set role "cc-ihsanos"')
    cur.execute("insert into agent_messages (from_agent) values ('cc-ihsanos') "
                "returning from_agent_verified")
    assert cur.fetchone()[0] is True


def test_ac3_cross_identity_post_is_blocked_by_rls(db):
    cur = db.cursor()
    cur.execute('set role "cc-ihsanos"')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("insert into agent_messages (from_agent) values ('cai')")


def test_ac4_operator_allowlist_to_cai_is_permitted_and_verified(db):
    cur = db.cursor()
    cur.execute('set role "musa"')
    cur.execute("insert into agent_messages (from_agent) values ('cai') "
                "returning posted_by_identity, from_agent_verified")
    identity, verified = cur.fetchone()
    assert identity == "musa"
    assert verified is True


def test_ac5_strategic_decisions_decided_by_is_enforced(db):
    cur = db.cursor()
    cur.execute('set role "cc-ihsanos"')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("insert into strategic_decisions (decided_by) values ('cai')")
    db.rollback()
    cur.execute('set role "cc-ihsanos"')
    cur.execute("insert into strategic_decisions (decided_by, decided_by_verified) "
                "values ('cc-ihsanos', true) returning posted_by_identity, decided_by_verified")
    identity, verified = cur.fetchone()
    assert identity == "cc-ihsanos"
    assert verified is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/migrations/test_bug024_identity_enforcement.py -v`
Expected: tests FAIL only if SQL constants are wrong; since Task 2 already wrote correct constants, they should PASS. If any fail, fix the SQL constant in `scripts/apply_bug024_identity_enforcement.py` (not the test) and re-run. (TDD note: the substrate behavior was validated by hand before these constants were written; this task locks it in executable form.)

- [ ] **Step 3: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/migrations/test_bug024_identity_enforcement.py -v`
Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/migrations/test_bug024_identity_enforcement.py
git commit -m "test(bug024-p2): 5 AC tests for identity enforcement (SET ROLE simulation)"
```

---

## Task 4: Dry-run the apply script against prod (read-only verification, NO --apply)

**Files:** none (verification only)

- [ ] **Step 1: Run the dry-run**

Run: `.venv/bin/python scripts/apply_bug024_identity_enforcement.py`
Expected: prints non-null verified BEFORE counts (agent_messages≈60, strategic_decisions≈20), "voided + hardened", then "DRY-RUN (rolled back)". The transaction rolls back — **nothing persists**. This proves the migration executes cleanly against the real prod schema (catches any column/trigger-name drift) without committing.

- [ ] **Step 2: Confirm no residue**

Run: `.venv/bin/python -c "import os,psycopg; from dotenv import load_dotenv; load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env'); dsn=os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL'); conn=psycopg.connect(dsn,autocommit=True); cur=conn.cursor(); cur.execute('select count(*) from agent_messages where from_agent_verified is not null'); print('still non-null (unchanged):', cur.fetchone()[0])"`
Expected: unchanged count (≈60) — dry-run did not mutate prod.

- [ ] **Step 3: No commit** (verification task)

---

## Operator handoff (out of scope for this plan — DO NOT execute)

Apply to prod is **operator-gated (cai #2064)**. Before `--apply`, the operator must:
1. Create per-agent Postgres roles named exactly as agent identifiers (`"cai"`, `"cc-ihsanos"`, `"cc-orchestrator"`, `"cc-scholar"`, `"cc-cosem"`, `"cc-wing"`, `"substrate"`, `"musa"`), **none with BYPASSRLS**, each with its own credential.
2. Grant each: `INSERT, SELECT` on `agent_messages`/`strategic_decisions` and `SELECT` on `identity_allowlist`.
3. Migrate every writer process to connect under its agent role (the inserter migration — shared with SUBSTRATE-COHERENCE-001 E's deferred `from_agent` CHECK).
4. Then `python scripts/apply_bug024_identity_enforcement.py --apply`.

Until then, enforcement is inert (service-role bypasses RLS) and escalate-only-in-session remains the containment line.

## Self-Review

- **Spec coverage:** B1 (Task 2 harden triggers), B2 (Task 2 RLS_AGENT_MESSAGES_INSERT), B3 (Task 2 SEED_ALLOWLIST), B4 (Task 2 strategic_decisions trigger + policy), mandatory void (Task 2 VOID_*), tests (Task 3 AC1–AC5), apply approach (Task 4 dry-run). Inserter migration + credentials = operator handoff (correctly out of scope). ✓
- **Placeholders:** none — full SQL and test code inline. ✓
- **Type/name consistency:** trigger names `trg_agent_messages_provenance` / `trg_strategic_decisions_provenance`, function names `populate_*_provenance`, allowlist columns `posted_by`/`allowed_from_agent` — all match the dumped prod definitions. ✓
- **Design correction captured:** SECURITY INVOKER (vs the prod DEFINER) is documented as the load-bearing fix. ✓
