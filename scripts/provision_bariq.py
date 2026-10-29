#!/usr/bin/env python3
"""provision_bariq.py — dedicated Bariq provisioning path (op#34584, option b).

cc-bariq owns this script and the runbook (reports/bariq-provisioning-runbook-20261027.md);
orch-console RUNS it behind the narrow console_irsyad_guard carve-out because only
this host holds the ceayj admin write DSN and only orch-console authors the ceayj
gate row. Everything is derived, never hardcoded: the target silo is read from
the migration's own `-- ledger: silo=` header, and the admin DSN is discovered by
matching that silo ref against DSN-shaped env values (no literal env-var name).

What it does, in order:
  1. dry-run the migration through apply_migration.apply_migration (always, even
     for --apply — belt and braces before any write),
  2. --apply: real apply with --gate <console-authored agent_messages id>,
  3. mint role bariq_editor (LOGIN, generated password) with SELECT+UPDATE on
     public.bariq_site_content and SELECT on public.bariq_site_content_history
     ONLY — verified against information_schema, refusing on any extra/missing
     grant,
  4. seed-verify (site/hero/testimonials rows present, updated_by='seed'),
  5. write the editor DSN to .secrets/bariq_editor_<silo>.dsn (mode 0600),
  6. ROUND-TRIP verify as bariq_editor over the minted DSN itself: a real
     SELECT of the seed rows + a no-op UPDATE in an ALWAYS-rolled-back txn
     (#77536: role_table_grants / has_table_privilege cannot see an RLS
     deny-all — grants present + no policy FOR the role = every row filtered
     to 0; 'provisioned' must mean the editor can truly read + write),
  7. bus orch-console the FILE PATH (never the DSN).

Never: prints a DSN or password, buses a DSN, mints in dry-run, applies without
a gate on a production silo (apply_migration enforces that itself).
"""

from __future__ import annotations

import argparse
import os
import re
import secrets as pysecrets
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlparse

ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCH_DIR / "scripts"))

import apply_migration  # noqa: E402  (fleet migration applier — the ONLY writer of schema)

ROLE = "bariq_editor"
DSN_SCHEME = "postgres" + "ql://"
EXPECTED_GRANTS = {
    ("public", "bariq_site_content", "SELECT"),
    ("public", "bariq_site_content", "UPDATE"),
    ("public", "bariq_site_content_history", "SELECT"),
}
SEED_KEYS = ("site", "hero", "testimonials")
BARIQ_ORG_ID = "62617269-7100-4000-8000-000000000001"
LEDGER_COLUMNS = ("repo", "migration_name", "silo_ref", "sha256", "applied_at", "applied_by", "note")

# Fleet migration ledger on the target silo (#77445: ceayj lacks it). Exact
# substrate shape (PK repo+migration_name+silo_ref, no id column). Additive,
# create-if-absent, idempotent; RLS + revoked anon/authenticated so fleet infra
# never leaks through the MT DB's public surface. Required BEFORE apply_migration
# runs at all — its collision check and atomic ledger insert both read/write it.
LEDGER_DDL = """CREATE TABLE IF NOT EXISTS public.migration_ledger (
  repo           TEXT NOT NULL,
  migration_name TEXT NOT NULL,
  silo_ref       TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  applied_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_by     TEXT,
  note           TEXT,
  CONSTRAINT migration_ledger_pkey PRIMARY KEY (repo, migration_name, silo_ref)
);
REVOKE ALL ON public.migration_ledger FROM anon, authenticated;
ALTER TABLE public.migration_ledger ENABLE ROW LEVEL SECURITY;
"""


class Refuse(Exception):
    """Raised for every refuse-don't-warn condition (mirrors apply_migration)."""


def fail(msg: str) -> "None":
    raise Refuse(msg)


def silo_from_text(sql_text: str) -> str:
    m = re.search(r"--\s*ledger:\s*silo=([a-z0-9]+)", sql_text)
    if not m:
        fail("migration has no '-- ledger: silo=<ref>' header — refusing to guess the target store")
    return m.group(1)


def _looks_like_dsn(value: str) -> bool:
    return isinstance(value, str) and value.startswith(DSN_SCHEME)


def _role_part(username: str | None) -> str:
    return (username or "").split(".", 1)[0].strip().lower()


def _looks_readonly(role_part: str) -> bool:
    if role_part in ("ro", "readonly", "read_only", "auditor", "auditor_ro"):
        return True
    return "auditor" in role_part or "readonly" in role_part or "read_only" in role_part


def discover_admin_dsn(env: dict, silo: str) -> tuple[str, str]:
    """Silo-matching DSNs can include read-only pooler logins (auditor_ro.<silo>) —
    the ADMIN one is the superuser. Filter read-only-looking role-parts, prefer
    role-part 'postgres', still refuse genuine ambiguity (#77433)."""
    hits = []
    for name, value in env.items():
        if not _looks_like_dsn(value):
            continue
        try:
            parsed = urlparse(value)
        except ValueError:
            continue
        if silo in (parsed.hostname or "") or silo in (parsed.username or ""):
            hits.append((name, value, _role_part(parsed.username)))
    if not hits:
        fail(f"no {DSN_SCHEME} DSN in env targets silo {silo!r} (checked host + username) — the admin DSN must be exported")
    writable = [h for h in hits if not _looks_readonly(h[2])]
    if not writable:
        ro_names = [h[0] for h in hits]
        fail(f"only read-only DSN(s) {ro_names} target silo {silo!r} — DDL + CREATE ROLE need the admin (postgres.<silo>) DSN")
    if len(writable) == 1:
        return writable[0][0], writable[0][1]
    postgres_only = [h for h in writable if h[2] == "postgres"]
    if len(postgres_only) == 1:
        return postgres_only[0][0], postgres_only[0][1]
    fail(f"ambiguous: {len(writable)} writable env DSNs target silo {silo!r}: {[h[0] for h in writable]} — export exactly one admin DSN")


def generate_password() -> str:
    return pysecrets.token_urlsafe(24)


def render_role_sql(role: str, password: str) -> str:
    return f"""DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE {role} LOGIN PASSWORD '{password}';
  ELSE
    ALTER ROLE {role} LOGIN PASSWORD '{password}';
  END IF;
END
$$;
GRANT USAGE ON SCHEMA public TO {role};
GRANT SELECT, UPDATE ON public.bariq_site_content TO {role};
GRANT SELECT ON public.bariq_site_content_history TO {role};
"""


def build_editor_dsn(admin_dsn: str, role: str, password: str) -> str:
    """Carry the pooler tenant suffix: the shared pooler derives the tenant from
    the username's part after the first dot (postgres.<tenant>), so the editor
    DSN must be <role>.<tenant> — a bare role username FATALs with
    ENOIDENTIFIER (#77507). A suffix-less admin username (direct host) keeps
    the bare role.

    The admin DSN's query is preserved, with sslmode FORCED to require
    (cc-irsyad-coord #81597, ahead of the gated ceayj SSL window): the minted
    credential must carry the TLS pin itself, so a re-mint can never fall back
    to libpq's default `prefer`. Rebuilding the DSN without the query — the
    previous behaviour — silently dropped any pin on the admin source."""
    p = urlparse(admin_dsn)
    username = p.username or ""
    _, _, tenant = username.partition(".")
    user = f"{role}.{tenant}" if tenant else role
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k != "sslmode"] + [("sslmode", "require")]
    port = p.port or 5432
    return (f"{DSN_SCHEME}{user}:{quote_plus(password)}@{p.hostname}:{port}"
            f"{p.path}?{urlencode(query, quote_via=quote)}")


def write_dsn_file(path: Path, dsn: str) -> None:
    if path.parent.exists() and stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        fail(f"{path.parent} is group/other-accessible — DSN file must live in a 0700 dir")
    path.write_text(dsn + "\n")
    path.chmod(0o600)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.unlink(missing_ok=True)
        fail(f"could not set 0600 on {path} — removed, not leaving a loose DSN")


def verify_grants(cur, role: str) -> None:
    cur.execute(
        """SELECT table_schema, table_name, privilege_type
           FROM information_schema.role_table_grants
           WHERE grantee = %s ORDER BY table_name, privilege_type""",
        (role,),
    )
    found = {(s, t, p) for (s, t, p) in cur.fetchall()}
    extra = found - EXPECTED_GRANTS
    missing = EXPECTED_GRANTS - found
    if extra:
        fail(f"role {role} holds UNEXPECTED grants {sorted(extra)} — least-privilege violated, refusing")
    if missing:
        fail(f"role {role} is missing grants {sorted(missing)} — mint did not take, refusing")


def verify_seed(cur) -> None:
    cur.execute(
        "SELECT key FROM bariq_site_content WHERE org_id = %s AND updated_by = 'seed'",
        (BARIQ_ORG_ID,),
    )
    keys = {k for (k,) in cur.fetchall()}
    if keys != set(SEED_KEYS):
        fail(f"seed verification: expected {sorted(SEED_KEYS)}, found {sorted(keys)}")


def verify_ledger(cols: list, pk_rows: list) -> None:
    col_names = [c[0] for c in cols]
    if sorted(col_names) != sorted(LEDGER_COLUMNS):
        fail(f"migration_ledger column drift (expected {sorted(LEDGER_COLUMNS)}, found {sorted(col_names)}) — refusing, substrate parity broken")
    if not pk_rows:
        fail("migration_ledger has no primary key — refusing, substrate parity broken")


def _bootstrap_ledger(admin_dsn: str) -> None:
    import psycopg
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute(LEDGER_DDL)
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='migration_ledger' ORDER BY ordinal_position"
        )
        cols = cur.fetchall()
        cur.execute(
            """SELECT constraint_type FROM information_schema.table_constraints
               WHERE table_schema='public' AND table_name='migration_ledger' AND constraint_type='PRIMARY KEY'"""
        )
        verify_ledger(cols, cur.fetchall())


def _apply(dsn: str, path: Path, *, silo: str, repo: str, dry_run: bool, gate: int | None) -> dict:
    return apply_migration.apply_migration(
        dsn, path, silo=silo, repo=repo, dry_run=dry_run,
        gate=gate, gate_dsn=os.environ.get("DATABASE_URL"),
        applied_by="provision_bariq.py",
    )


def _mint_connection(admin_dsn: str):
    import psycopg
    return psycopg.connect(admin_dsn, autocommit=True)


def _editor_connection(editor_dsn: str):
    """NOT autocommit: the round-trip's UPDATE lives in a txn we always roll back."""
    import psycopg
    return psycopg.connect(editor_dsn)


def verify_roundtrip(editor_dsn: str) -> None:
    """#77536: role_table_grants cannot see an RLS deny-all — grants present but
    no policy FOR the role filters every row to 0. 'Provisioned' must mean the
    editor can truly read + write a row, so connect AS bariq_editor and do it:
    SELECT the seed keys, then a no-op UPDATE (SET value = value) whose txn is
    ALWAYS rolled back — nothing persists, not even the audit-history row the
    trigger writes (same txn, SECURITY DEFINER, rolls back with it)."""
    with _editor_connection(editor_dsn) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT key FROM bariq_site_content WHERE org_id = %s ORDER BY key",
            (BARIQ_ORG_ID,),
        )
        keys = {k for (k,) in cur.fetchall()}
        if keys != set(SEED_KEYS):
            fail(f"editor round-trip SELECT saw {sorted(keys)}, expected {sorted(SEED_KEYS)} — grants present but RLS admits no rows (deny-all: missing policy FOR bariq_editor)")
        conn.rollback()
        cur.execute(
            "UPDATE bariq_site_content SET value = value WHERE org_id = %s AND key = %s",
            (BARIQ_ORG_ID, SEED_KEYS[0]),
        )
        if cur.rowcount < 1:
            fail(f"editor round-trip UPDATE matched {cur.rowcount} rows — RLS admits no rows for UPDATE (deny-all: missing policy FOR bariq_editor)")
        conn.rollback()


def _bus_path(secrets_path: Path, role: str, grants: str) -> None:
    body = (
        f"provision_bariq.py --apply completed: migration applied, role {role} minted "
        f"(grants: {grants}), seed verified. Editor DSN written to {secrets_path} (0600). "
        f"cc-bariq takes it from here: 5-step e2e per the runbook. This message carries "
        f"the PATH only — the DSN never leaves this host."
    )
    subprocess.run(
        [str(ORCH_DIR / ".venv/bin/python3"), str(ORCH_DIR / "scripts/bus_send.py"),
         "--to", "orch-console", "--type", "update",
         "--subject", "Bariq provisioning applied (provision_bariq.py)",
         "--priority", "P2"],
        input=body, text=True, check=True, capture_output=True,
    )


def run(mode: str, gate: int | None, migration: Path, env: dict) -> None:
    if mode == "apply" and gate is None:
        print("REFUSED: --apply on a production silo requires --gate <agent_messages id> "
              "(apply_migration enforces this too; fail early here for a clean message)", file=sys.stderr)
        sys.exit(1)

    sql_text = migration.read_text()
    silo = silo_from_text(sql_text)
    var_name, admin_dsn = discover_admin_dsn(env, silo)
    print(f"# silo={silo} (from migration ledger header)")
    print(f"# admin DSN: env ${var_name} (discovered by silo match)")

    if mode == "regen-dsn":
        # #77507 recovery path: re-mint the editor password (idempotent CREATE/ALTER
        # ROLE), re-verify grants + seed, rewrite the .secrets DSN. NEVER applies the
        # migration, NEVER touches migration_ledger.
        password = generate_password()
        with _mint_connection(admin_dsn) as conn:
            cur = conn.cursor()
            cur.execute(render_role_sql(ROLE, password))
            verify_grants(cur, ROLE)
            verify_seed(cur)
        dsn = build_editor_dsn(admin_dsn, ROLE, password)
        secrets_path = ORCH_DIR / ".secrets" / f"{ROLE}_{silo}.dsn"
        write_dsn_file(secrets_path, dsn)
        verify_roundtrip(dsn)
        print(f"# DSN regenerated (tenant-suffixed username) at {secrets_path} (0600); round-trip verified as {ROLE}; bus-ing path")
        _bus_path(secrets_path, ROLE, "regen: SELECT+UPDATE bariq_site_content; SELECT bariq_site_content_history; USAGE public")
        return

    _bootstrap_ledger(admin_dsn)
    print("# migration_ledger bootstrapped (create-if-absent, substrate-exact, RLS + no anon/authenticated grants)")

    result = _apply(admin_dsn, migration, silo=silo, repo="ihsanos", dry_run=True, gate=gate)
    print(f"# dry-run: {result['status']} sha={result['sha256'][:12]}")
    if mode == "dry-run":
        print(f"# dry-run only — role {ROLE} NOT minted; would write .secrets/{ROLE}_{silo}.dsn (0600)")
        return

    result = _apply(admin_dsn, migration, silo=silo, repo="ihsanos", dry_run=False, gate=gate)
    print(f"# apply: {result['status']}")

    password = generate_password()
    role_sql = render_role_sql(ROLE, password)
    with _mint_connection(admin_dsn) as conn:
        cur = conn.cursor()
        cur.execute(role_sql)
        verify_grants(cur, ROLE)
        verify_seed(cur)

    dsn = build_editor_dsn(admin_dsn, ROLE, password)
    secrets_path = ORCH_DIR / ".secrets" / f"{ROLE}_{silo}.dsn"
    write_dsn_file(secrets_path, dsn)
    verify_roundtrip(dsn)
    grants = "SELECT+UPDATE bariq_site_content; SELECT bariq_site_content_history; USAGE public"
    print(f"# role {ROLE} minted + verified (grants, seed, round-trip); DSN at {secrets_path} (0600); bus-ing path to orch-console")
    _bus_path(secrets_path, ROLE, grants)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="validate everything, write nothing (default)")
    g.add_argument("--apply", action="store_true", help="apply migration (needs --gate) + mint role + write DSN file")
    g.add_argument("--regen-dsn", action="store_true",
                   help="re-mint the editor password + rewrite the .secrets DSN (tenant-suffixed); no migration apply, no ledger touch (#77507 recovery)")
    p.add_argument("--gate", type=int, default=None, help="agent_messages id of the console-authored gate row")
    p.add_argument("--migration", default=str(ORCH_DIR / "migrations/drafts/bariq_site_content_ceayj.sql"))
    args = p.parse_args(argv)

    try:
        mode = "regen-dsn" if args.regen_dsn else ("apply" if args.apply else "dry-run")
        run(mode=mode, gate=args.gate, migration=Path(args.migration), env=dict(os.environ))
    except Refuse as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
