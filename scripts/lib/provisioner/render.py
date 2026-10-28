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
import re
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

_LEDGER_DDL = """CREATE TABLE IF NOT EXISTS public.migration_ledger (
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

_PIN_RE = re.compile(r"^CREATE POLICY .*?(?=^\s*(?:DROP|CREATE|REVOKE|GRANT|ALTER|INSERT|--)|\Z)",
                     re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Bundle:
    name: str
    silo_ref: str
    forward_sql: str
    revert_sql: str
    sha256: str


def _fmt(template: str, ctx: dict) -> str:
    return template.format(**ctx)


def _org_pin_scan(forward_sql: str, org_uuid: str) -> None:
    for policy in _PIN_RE.findall(forward_sql):
        if f"org_id = '{org_uuid}'" not in policy:
            raise Refuse(
                "rendered policy without the client's org pin (plan §4.3):\n"
                f"{policy.strip()[:200]}"
            )


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
        "-- 1. The client as an organization (tenant)",
        f"INSERT INTO organizations (id, name, uen, type, slug, settings)",
        f"VALUES ('{spec.org_uuid}',",
        f"        {sql_str(spec.org_name)},",
        f"        {sql_str(spec.org_uen)},",
        "        'business',",
        f"        {sql_str(spec.org_slug)},",
        "        '{}')",
        "ON CONFLICT (id) DO NOTHING;",
        "",
        "-- 2. Content store: one JSONB document per editable key, org-scoped",
        f"CREATE TABLE {spec.content_table} (",
        "  org_id     UUID NOT NULL REFERENCES organizations(id),",
        "  key        TEXT NOT NULL,",
        "  value      JSONB NOT NULL,",
        "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),",
        "  updated_by TEXT NOT NULL DEFAULT 'system',",
        "  PRIMARY KEY (org_id, key)",
        ");",
        "",
        "-- 3. Append-only history (fleet-private)",
        f"CREATE TABLE {spec.history_table} (",
        "  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,",
        "  org_id     UUID NOT NULL REFERENCES organizations(id),",
        "  key        TEXT NOT NULL,",
        "  old_value  JSONB,",
        "  new_value  JSONB NOT NULL,",
        "  changed_by TEXT NOT NULL,",
        "  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ");",
        "",
        f"CREATE FUNCTION {spec.content_table}_audit() RETURNS TRIGGER AS $$",
        "BEGIN",
        f"  INSERT INTO {spec.history_table} (org_id, key, old_value, new_value, changed_by)",
        "  VALUES (NEW.org_id, NEW.key, OLD.value, NEW.value, NEW.updated_by);",
        "  RETURN NEW;",
        "END;",
        "$$ LANGUAGE plpgsql SECURITY DEFINER",
        "SET search_path = public;",
        "",
        f"CREATE TRIGGER {spec.content_table}_audit_trg",
        f"AFTER UPDATE ON {spec.content_table}",
        f"FOR EACH ROW EXECUTE FUNCTION {spec.content_table}_audit();",
        "",
        "-- 4. Privileges + RLS: revoke the pg_default_acl ALLs, grant back exactly",
        "--    the least-priv surfaces, and pin every policy to the client's org.",
        f"REVOKE ALL ON {spec.content_table} FROM anon, authenticated;",
        f"GRANT SELECT ON {spec.content_table} TO anon;",
        f"REVOKE ALL ON {spec.history_table} FROM anon, authenticated;",
        f"REVOKE ALL ON FUNCTION {spec.content_table}_audit() FROM anon, authenticated;",
        "",
        f"ALTER TABLE {spec.content_table} ENABLE ROW LEVEL SECURITY;",
        f"ALTER TABLE {spec.history_table} ENABLE ROW LEVEL SECURITY;",
        "",
        *[
            f"CREATE POLICY {pname.format(**ctx)}\n  {pbody.format(**ctx)};"
            for pname, pbody in _POLICY_TEMPLATES
        ],
        "",
        "-- 5. Editor role: NOLOGIN placeholder (mint-editor-dsn sets the LOGIN",
        "--    password later, off-artifact — a password never lands in a bundle).",
        f"DO $$ BEGIN",
        f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{spec.editor_role}') THEN",
        f"    CREATE ROLE {spec.editor_role} NOLOGIN;",
        "  END IF;",
        "END $$;",
        f"GRANT USAGE ON SCHEMA public TO {spec.editor_role};",
        f"GRANT SELECT, UPDATE ON public.{spec.content_table} TO {spec.editor_role};",
        f"GRANT SELECT ON public.{spec.history_table} TO {spec.editor_role};",
        "",
        "-- 6. migration_ledger bootstrap (#77445) — apply_migration's collision",
        "--    check reads this before any apply can run.",
        _LEDGER_DDL,
    ])) + "\n"

    _org_pin_scan(forward, spec.org_uuid)

    revert = "\n".join([
        f"-- REVERT twin of {name} (plan §6: same paper trail backward).",
        f"-- Dependency order: policies -> tables -> role -> org row.",
        "-- The migration_ledger stays: it is store-level fleet infra, not the client's.",
        "--",
        *[f"DROP POLICY IF EXISTS {pname.format(**ctx)} ON public.{_policy_table(pname, ctx)};"
          for pname, _ in _POLICY_TEMPLATES],
        f"DROP TRIGGER IF EXISTS {spec.content_table}_audit_trg ON public.{spec.content_table};",
        f"DROP FUNCTION IF EXISTS public.{spec.content_table}_audit();",
        f"DROP TABLE IF EXISTS public.{spec.history_table};",
        f"DROP TABLE IF EXISTS public.{spec.content_table};",
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


def sql_str(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"
