"""render.py — the only place client SQL is authored (plan §0/§1: verbs
render their own SQL; nothing crosses the wire).

render_bundle(spec) emits ONE artifact per client: the forward provisioning
bundle (org row, content + history tables, audit trail, least-priv anon and
editor surfaces with their org-pinned per-command policies — the #77536
grant-without-policy deny-all class baked out at design time — the editor
role as NOLOGIN, and the migration_ledger bootstrap #77445) plus its REVERT
twin in dependency order (policies -> tables -> role -> org row, plan §6),
and the sha256 the gate binds to.

The bundle's silo ref comes from the registry via the spec's alias — never
hand-typed. After rendering, the renderer re-scans its OWN output: any
CREATE POLICY without the client's org pin is a Refuse (plan §4.3,
structural org-pinning — the check exists to catch template drift, not
caller error).
"""
from __future__ import annotations

import hashlib

from dataclasses import dataclass

from .registry import Refuse, store_ref
from .spec import ClientSpec

# (policy name, body). Every body MUST contain the {org_uuid} pin — see
# _org_pin_scan below.
_POLICY_TEMPLATES: list[tuple[str, str]] = [
    ("{content_table}_public_read",
     "ON {content_table} FOR SELECT TO anon USING (org_id = '{org_uuid}')"),
    ("{content_table}_editor_select",
     "ON {content_table} FOR SELECT TO {editor_role} USING (org_id = '{org_uuid}')"),
    ("{content_table}_editor_update",
     "ON {content_table} FOR UPDATE TO {editor_role} USING (org_id = '{org_uuid}') "
     "WITH CHECK (org_id = '{org_uuid}')"),
    ("{history_table}_editor_select",
     "ON {history_table} FOR SELECT TO {editor_role} USING (org_id = '{org_uuid}')"),
]

# The org-row marker the store's fleet_provisioner INSERT/DELETE policies pin
# (migration ceayj_adopt_bariq_provisioner): an INSERT policy carries no USING
# so it widens no read path, and a DELETE USING on the marker can only ever
# match rows a provisioning bundle created. KNOWN LIMIT (console #80298): a
# tenant org_admin can still edit its own settings — the marker is not
# tamper-proof against that tenant; fp acts only through gated verbs.
_ORG_MARKER_SETTINGS = '{"managed_by":"wingmen_provisioner"}'

# Exact-shape pins for the idempotency guards (#80298 condition 3): a
# same-named object with a DIFFERENT shape refuses, never silently skips.
# information_schema data_type spellings, ordinal order, comma-joined.
_CONTENT_SHAPE = ("org_id:uuid,key:text,value:jsonb,"
                  "updated_at:timestamp with time zone,updated_by:text")
_HISTORY_SHAPE = ("id:bigint,org_id:uuid,key:text,old_value:jsonb,new_value:jsonb,"
                  "changed_by:text,changed_at:timestamp with time zone")

_LEDGER_CREATE = """CREATE TABLE public.migration_ledger (
  repo           TEXT NOT NULL,
  migration_name TEXT NOT NULL,
  silo_ref       TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  applied_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_by     TEXT,
  note           TEXT,
  CONSTRAINT migration_ledger_pkey PRIMARY KEY (repo, migration_name, silo_ref)
)"""


@dataclass(frozen=True)
class Bundle:
    name: str
    silo_ref: str
    forward_sql: str
    revert_sql: str
    sha256: str


def _org_pin_scan(policy_statements: list[str], org_uuid: str) -> None:
    for policy in policy_statements:
        if f"org_id = '{org_uuid}'" not in policy:
            raise Refuse(
                "rendered policy without the client's org pin (plan §4.3):\n"
                f"{policy.strip()[:200]}"
            )


def _table_guard(create_ddl: str, table: str, shape: str) -> str:
    """Idempotent + exact (#80298 condition 3): create when absent, ACCEPT
    only the exact column shape when present — anything else refuses."""
    return (
        "DO $$\n"
        "BEGIN\n"
        f"  IF to_regclass('public.{table}') IS NULL THEN\n"
        f"    EXECUTE $ddl${create_ddl}$ddl$;\n"
        "  ELSIF (SELECT string_agg(column_name || ':' || data_type, ',' ORDER BY ordinal_position)\n"
        "           FROM information_schema.columns\n"
        f"          WHERE table_schema='public' AND table_name='{table}')\n"
        f"        IS DISTINCT FROM '{shape}' THEN\n"
        f"    RAISE EXCEPTION 'SHAPE-REFUSED: public.{table} exists with a different shape "
        "than this render (orch-console #80298 condition 3)';\n"
        "  END IF;\n"
        "END $$;")


def _fn_guard(create_ddl: str, fn_name: str, fn_source: str) -> str:
    """Same contract as _table_guard for the audit function: the shape pin is
    the whitespace-normalized source, so a same-named function with a
    different body refuses. Outer tag is $fng$ (not $$) — the audit fn's
    own body carries $$ quotes, which would close a plain-$$ DO early."""
    normalized = " ".join(fn_source.split())
    return (
        "DO $fng$\n"
        "BEGIN\n"
        "  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace\n"
        f"                  WHERE n.nspname='public' AND p.proname='{fn_name}') THEN\n"
        f"    EXECUTE $ddl${create_ddl}$ddl$;\n"
        "  ELSIF (SELECT btrim(regexp_replace(p.prosrc, '\\s+', ' ', 'g'))\n"
        "           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace\n"
        f"          WHERE n.nspname='public' AND p.proname='{fn_name}')\n"
        f"        IS DISTINCT FROM '{normalized}' THEN\n"
        f"    RAISE EXCEPTION 'SHAPE-REFUSED: function {fn_name}() exists with a different "
        "body than this render (orch-console #80298 condition 3)';\n"
        "  END IF;\n"
        "END $fng$;")


def _trigger_guard(create_ddl: str, trigger: str, table: str) -> str:
    return (
        "DO $$\n"
        "BEGIN\n"
        "  IF NOT EXISTS (SELECT 1 FROM pg_trigger\n"
        f"                  WHERE tgname='{trigger}'\n"
        f"                    AND tgrelid='public.{table}'::regclass AND NOT tgisinternal) THEN\n"
        f"    EXECUTE $ddl${create_ddl}$ddl$;\n"
        "  END IF;\n"
        "END $$;")


def _sq(v: str) -> str:
    """Embed a value inside a single-quoted SQL literal (the deparsed policy
    expressions carry quotes around the org uuid)."""
    return v.replace("'", "''")


def _policy_guard(statement: str, table: str, policy: str,
                  expected_qual: str, expected_check: str | None) -> str:
    """Idempotent + exact (#80298 condition 3): pg_policies deparse equality —
    a same-name policy with a different USING/WITH CHECK refuses instead of
    being silently skipped."""
    check_clause = ("c IS NOT NULL" if expected_check is None
                    else f"c IS DISTINCT FROM '{_sq(expected_check)}'")
    return (
        "DO $$\n"
        "DECLARE q text; c text;\n"
        "BEGIN\n"
        "  SELECT pol.qual, pol.with_check INTO q, c FROM pg_policies pol\n"
        f"   WHERE pol.schemaname='public' AND pol.tablename='{table}' AND pol.policyname='{policy}';\n"
        "  IF q IS NULL THEN\n"
        f"    EXECUTE $ddl${statement}$ddl$;\n"
        f"  ELSIF q IS DISTINCT FROM '{_sq(expected_qual)}' OR {check_clause} THEN\n"
        f"    RAISE EXCEPTION 'SHAPE-REFUSED: policy {policy} on public.{table} exists with a "
        "different expression than this render (orch-console #80298 condition 3)';\n"
        "  END IF;\n"
        "END $$;")


def render_bundle(spec: ClientSpec) -> Bundle:
    silo = store_ref(spec.alias)
    ctx = {
        "client_key": spec.client_key,
        "org_uuid": spec.org_uuid,
        "org_name": spec.org_name,
        "org_uen": spec.org_uen,
        "org_slug": spec.org_slug,
        "editor_role": spec.editor_role,
        "content_table": spec.content_table,
        "history_table": spec.history_table,
        "silo": silo,
    }
    name = f"{spec.client_key}_provisioning.sql"

    def asserts(role: str, table: str) -> list[str]:
        return [f"-- assert: no_table_privilege {role} public.{table} {cmd}"
                for cmd in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")]

    forward = "\n".join(filter(None, [
        f"-- {name}",
        f"-- ledger: silo={silo}",
        f"-- Rendered by provisioner.render_bundle (op#34565 option b, gate #78463).",
        "-- Refuse-don't-warn rendered artifact; applied only via the gated apply verb.",
        "--",
        "-- assert lines (lint: the REVOKEs below require them):",
        *asserts("anon", spec.content_table),
        *asserts("authenticated", spec.content_table),
        *[f"-- assert: no_table_privilege anon public.{spec.history_table} SELECT",
          *asserts("anon", spec.history_table)],
        *asserts("authenticated", spec.history_table),
        *asserts(spec.editor_role, spec.content_table),
        *asserts(spec.editor_role, spec.history_table),
        "--",
        "",
        "-- 1. The client as an organization (tenant). settings carries the",
        "--    managed_by marker: the store's fleet_provisioner INSERT/DELETE",
        "--    policies pin it (migration ceayj_adopt_bariq_provisioner), so",
        "--    a bundle can only ever create/delete its OWN marked rows.",
        f"INSERT INTO organizations (id, name, uen, type, slug, settings)",
        f"VALUES ('{spec.org_uuid}',",
        f"        {sql_str(spec.org_name)},",
        f"        {sql_str(spec.org_uen)},",
        "        'business',",
        f"        {sql_str(spec.org_slug)},",
        f"        '{_ORG_MARKER_SETTINGS}')",
        "ON CONFLICT (id) DO NOTHING;",
        "",
        "-- 2. Content store + history + audit fn/trigger: idempotent + EXACT",
        "--    (#80298 condition 3) — absent -> created, present -> accepted",
        "--    only at the exact rendered shape, anything else refuses.",
        _table_guard(
            f"CREATE TABLE {spec.content_table} (\n"
            "  org_id     UUID NOT NULL REFERENCES organizations(id),\n"
            "  key        TEXT NOT NULL,\n"
            "  value      JSONB NOT NULL,\n"
            "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n"
            "  updated_by TEXT NOT NULL DEFAULT 'system',\n"
            "  PRIMARY KEY (org_id, key)\n"
            ");", spec.content_table, _CONTENT_SHAPE),
        "",
        "-- 3. Append-only history (fleet-private)",
        _table_guard(
            f"CREATE TABLE {spec.history_table} (\n"
            "  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,\n"
            "  org_id     UUID NOT NULL REFERENCES organizations(id),\n"
            "  key        TEXT NOT NULL,\n"
            "  old_value  JSONB,\n"
            "  new_value  JSONB NOT NULL,\n"
            "  changed_by TEXT NOT NULL,\n"
            "  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()\n"
            ");", spec.history_table, _HISTORY_SHAPE),
        "",
        _fn_guard(
            f"CREATE FUNCTION {spec.content_table}_audit() RETURNS TRIGGER AS $$\n"
            f"{_FN_SOURCE(ctx)}"
            "$$ LANGUAGE plpgsql SECURITY DEFINER\n"
            "SET search_path = public;",
            f"{spec.content_table}_audit", _FN_SOURCE(ctx)),
        "",
        _trigger_guard(
            f"CREATE TRIGGER {spec.content_table}_audit_trg\n"
            f"AFTER UPDATE ON {spec.content_table}\n"
            f"FOR EACH ROW EXECUTE FUNCTION {spec.content_table}_audit();",
            f"{spec.content_table}_audit_trg", spec.content_table),
        "",
        "-- 4. Privileges + RLS: revoke the pg_default_acl ALLs, grant back exactly",
        "--    the least-priv surfaces, and pin every policy to the client's org.",
        f"REVOKE ALL ON {spec.content_table} FROM anon, authenticated;",
        f"GRANT SELECT ON {spec.content_table} TO anon;",
        f"REVOKE ALL ON {spec.history_table} FROM anon, authenticated;",
        f"REVOKE ALL ON FUNCTION {spec.content_table}_audit() FROM anon, authenticated, PUBLIC;",
        "",
        f"ALTER TABLE {spec.content_table} ENABLE ROW LEVEL SECURITY;",
        f"ALTER TABLE {spec.history_table} ENABLE ROW LEVEL SECURITY;",
        "",
        "-- 5a. Editor role FIRST (NOLOGIN placeholder; mint-editor-dsn sets the",
        "--    LOGIN password later, off-artifact). The org-pinned policies below",
        "--    name this role — on a fresh store it must exist before they render.",
        f"DO $$ BEGIN",
        f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{spec.editor_role}') THEN",
        f"    CREATE ROLE {spec.editor_role} NOLOGIN;",
        "  END IF;",
        "END $$;",
        "-- policies: idempotent + EXACT (#80298 condition 3) — a same-name",
        "-- policy with a different USING/WITH CHECK refuses, never skips.",
        *[
            _policy_guard(
                f"CREATE POLICY {pname.format(**ctx)}\n  {pbody.format(**ctx)};",
                _policy_table(pname, ctx),
                pname.format(**ctx),
                _expected_qual(ctx),
                _expected_qual(ctx) if "WITH CHECK" in pbody else None)
            for pname, pbody in _POLICY_TEMPLATES
        ],
        "",
        "-- 5. Editor role: NOLOGIN placeholder (mint-editor-dsn sets the LOGIN",
        "--    password later, off-artifact — a password never lands in a bundle).",
        f"GRANT USAGE ON SCHEMA public TO {spec.editor_role};",
        f"GRANT SELECT, UPDATE ON public.{spec.content_table} TO {spec.editor_role};",
        f"GRANT SELECT ON public.{spec.history_table} TO {spec.editor_role};",
        "",
        "-- 6. migration_ledger bootstrap (#77445) — apply_migration's collision",
        "--    check reads this before any apply can run. Fully existence-guarded",
        "--    (#80291): a pre-existing ledger is store infra the bundle must",
        "--    NOT touch (the REVOKE/ENABLE-RLS would need ownership it must",
        "--    never have), so those statements run only on the create path.",
        "DO $$",
        "BEGIN",
        "  IF to_regclass('public.migration_ledger') IS NULL THEN",
        f"    EXECUTE $ddl${_LEDGER_CREATE};$ddl$;",
        "    EXECUTE 'REVOKE ALL ON public.migration_ledger FROM anon, authenticated';",
        "    EXECUTE 'ALTER TABLE public.migration_ledger ENABLE ROW LEVEL SECURITY';",
        "  END IF;",
        "END $$;",
    ])) + "\n"

    _org_pin_scan(_policy_statements(ctx), spec.org_uuid)

    revert = "\n".join([
        f"-- REVERT twin of {name} (plan §6: same paper trail backward).",
        f"-- Dependency order: snapshot-guard -> policies -> tables -> role -> org row.",
        "-- The migration_ledger stays: it is store-level fleet infra, not the client's.",
        "--",
        "-- #80298 condition 1, fail-closed: dropping tables that hold data is",
        "-- refused unless a VERIFIED snapshot matches the LIVE row counts —",
        "-- the daemon's revert flow writes the marker (0600 file re-read +",
        "-- count match) in the same transaction; a stale or hand-typed marker",
        "-- never matches, so it authorizes nothing.",
        _revert_snapshot_guard(ctx),
        *[f"DROP POLICY IF EXISTS {pname.format(**ctx)} ON public.{_policy_table(pname, ctx)};"
          for pname, _ in _POLICY_TEMPLATES],
        f"DROP TRIGGER IF EXISTS {spec.content_table}_audit_trg ON public.{spec.content_table};",
        f"DROP FUNCTION IF EXISTS public.{spec.content_table}_audit();",
        f"DROP TABLE IF EXISTS public.{spec.history_table};",
        f"DROP TABLE IF EXISTS public.{spec.content_table};",
        "-- table grants die with their tables above; SCHEMA USAGE outlives",
        "-- them, and a role holding it cannot be dropped (DependentObjectsStillExist).",
        "-- Existence-guarded: REVOKE on an already-dropped role is UndefinedObject,",
        "-- and a second revert (a no-op everywhere else) must stay a no-op here too.",
        f"DO $$ BEGIN",
        f"  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{spec.editor_role}') THEN",
        f"    REVOKE ALL ON SCHEMA public FROM {spec.editor_role};",
        "  END IF;",
        "END $$;",
        f"DROP ROLE IF EXISTS {spec.editor_role};",
        f"DELETE FROM organizations WHERE id = '{spec.org_uuid}';",
    ]) + "\n"

    return Bundle(
        name=name,
        silo_ref=silo,
        forward_sql=forward,
        revert_sql=revert,
        sha256=hashlib.sha256(forward.encode()).hexdigest(),
    )


def _policy_table(pname_template: str, ctx: dict) -> str:
    """The table a policy template names (content or history)."""
    pname = pname_template.format(**ctx)
    if pname.startswith(ctx["history_table"]):
        return ctx["history_table"]
    return ctx["content_table"]


def _policy_statements(ctx: dict) -> list[str]:
    """The bare CREATE POLICY statements, pre-embedding — the org-pin scan
    (plan §4.3) runs on THESE, not on the guarded SQL that carries them."""
    return [f"CREATE POLICY {pname.format(**ctx)}\n  {pbody.format(**ctx)};"
            for pname, pbody in _POLICY_TEMPLATES]


def _expected_qual(ctx: dict) -> str:
    """The deparse pg_policies carries for `USING (org_id = '<uuid>')`:
    `(org_id = '<uuid>'::uuid)` — pinned against the live store 2026-10-28."""
    return f"(org_id = '{ctx['org_uuid']}'::uuid)"


def _FN_SOURCE(ctx: dict) -> str:
    return ("\nBEGIN\n"
            f"  INSERT INTO {ctx['history_table']} (org_id, key, old_value, new_value, changed_by)\n"
            "  VALUES (NEW.org_id, NEW.key, OLD.value, NEW.value, NEW.updated_by);\n"
            "  RETURN NEW;\n"
            "END;\n")


def _revert_snapshot_guard(ctx: dict) -> str:
    return (
        "DO $$\n"
        "DECLARE n_content bigint := 0; n_history bigint := 0;\n"
        "BEGIN\n"
        f"  IF to_regclass('public.{ctx['content_table']}') IS NOT NULL THEN\n"
        f"    SELECT count(*) INTO n_content FROM public.{ctx['content_table']};\n"
        "  END IF;\n"
        f"  IF to_regclass('public.{ctx['history_table']}') IS NOT NULL THEN\n"
        f"    SELECT count(*) INTO n_history FROM public.{ctx['history_table']};\n"
        "  END IF;\n"
        "  IF n_content > 0 OR n_history > 0 THEN\n"
        "    IF NOT EXISTS (SELECT 1 FROM public.provisioner_revert_snapshots\n"
        f"                    WHERE client_key = '{ctx['client_key']}' AND verified\n"
        "                      AND content_rows = n_content AND history_rows = n_history) THEN\n"
        "      RAISE EXCEPTION 'REVERT-REFUSED: public.%/public.% hold % and % rows "
        "and no verified snapshot matches the live row counts — snapshot via the "
        "daemon revert flow first (orch-console #80298 condition 1)',\n"
        f"        '{ctx['content_table']}', '{ctx['history_table']}', n_content, n_history;\n"
        "    END IF;\n"
        "  END IF;\n"
        "END $$;")


def sql_str(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"
