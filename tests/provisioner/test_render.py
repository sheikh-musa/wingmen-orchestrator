"""render_bundle — verbs render their own SQL (plan §0/§1): the bundle is
the ONLY artifact, org-pinned structurally, with its REVERT twin in the
same change (plan §6). No SQL ever crosses the wire.
"""
import hashlib
import re
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import render as render_mod  # noqa: E402
from provisioner.registry import Refuse, store_ref  # noqa: E402
from provisioner.spec import load_spec  # noqa: E402

BARIQ = load_spec(ORCH_DIR / "scripts" / "lib" / "provisioner" / "specs" / "bariq.json")


def test_bundle_header_carries_the_registry_ref():
    bundle = render_mod.render_bundle(BARIQ)
    assert bundle.name == "bariq_provisioning.sql"
    # the ref flows from the registry through the loader — never hand-typed
    assert f"-- ledger: silo={store_ref(BARIQ.alias)}" in bundle.forward_sql


def test_every_policy_is_org_pinned():
    # the structural scan runs on the BARE statements (plan §4.3) — the
    # forward embeds them inside idempotency guards (#80298 condition 3),
    # so extract the same list the renderer itself scans
    bundle = render_mod.render_bundle(BARIQ)
    ctx = {f: getattr(BARIQ, f) for f in
           ("org_uuid", "editor_role", "content_table", "history_table")}
    policies = render_mod._policy_statements(ctx)
    assert len(policies) >= 4  # anon read + editor select/update + history select
    for p in policies:
        assert f"org_id = '{BARIQ.org_uuid}'" in p, f"unpinned policy:\n{p}"
        # and each statement is actually carried into the guarded forward
        assert p in bundle.forward_sql, f"policy not embedded in forward:\n{p}"


def test_forward_is_exact_shape_guarded_not_bare_ddl():
    # #80298 condition 3: same-name/different-shape objects must REFUSE, so
    # no bare CREATE POLICY/CREATE TABLE may remain — everything object-
    # creating sits inside a guard that pins the expected shape
    forward = render_mod.render_bundle(BARIQ).forward_sql
    assert "SHAPE-REFUSED" in forward
    assert not re.findall(r"(?m)^CREATE (?:POLICY|TABLE) ", forward), \
        "bare DDL escaped the idempotency guards"
    # the org INSERT carries the marker the store's fp policies pin
    assert '"managed_by":"wingmen_provisioner"' in forward


def test_revert_is_snapshot_guarded_before_any_drop():
    # #80298 condition 1: the guard runs FIRST — before the first DROP
    revert = render_mod.render_bundle(BARIQ).revert_sql
    assert "REVERT-REFUSED" in revert
    assert revert.index("REVERT-REFUSED") < revert.index("DROP POLICY IF EXISTS")


def test_migration_ledger_create_is_existence_guarded():
    # #80291: a pre-existing ledger is store infra — the REVOKE/ENABLE-RLS
    # statements must run only on the create path (they need an ownership
    # the bundle must never assume), so exactly one bare CREATE, inside
    # the EXECUTE, and guarded by to_regclass
    forward = render_mod.render_bundle(BARIQ).forward_sql
    assert "IF to_regclass('public.migration_ledger') IS NULL THEN" in forward
    assert forward.count("CREATE TABLE public.migration_ledger") == 1
    assert forward.count("REVOKE ALL ON public.migration_ledger FROM anon, authenticated") == 1


def test_editor_surface_is_least_privilege():
    forward = render_mod.render_bundle(BARIQ).forward_sql
    assert f"GRANT USAGE ON SCHEMA public TO {BARIQ.editor_role}" in forward
    assert f"GRANT SELECT, UPDATE ON public.{BARIQ.content_table} TO {BARIQ.editor_role}" in forward
    assert f"GRANT SELECT ON public.{BARIQ.history_table} TO {BARIQ.editor_role}" in forward
    # INSERT/DELETE stay impossible everywhere for the editor
    editor_inserts = re.findall(r"GRANT[^\n]*INSERT[^\n]*" + BARIQ.editor_role, forward)
    assert not editor_inserts


def test_assert_header_lines_present():
    forward = render_mod.render_bundle(BARIQ).forward_sql
    assert "-- assert: no_table_privilege anon public.bariq_site_content INSERT" in forward
    assert "-- assert: no_table_privilege authenticated public.bariq_site_content TRUNCATE" in forward
    assert f"-- assert: no_table_privilege {BARIQ.editor_role} public.bariq_site_content INSERT" in forward
    assert f"-- assert: no_table_privilege {BARIQ.editor_role} public.bariq_site_content_history TRUNCATE" in forward


def test_org_row_insert_is_idempotent():
    forward = render_mod.render_bundle(BARIQ).forward_sql
    m = re.search(r"INSERT INTO organizations \(id, name, uen, type, slug, settings\)(.*?)ON CONFLICT \(id\) DO NOTHING;", forward, re.DOTALL)
    assert m, "org INSERT must be ON CONFLICT (id) DO NOTHING (idempotent)"
    assert f"'{BARIQ.org_uuid}'" in m.group(1)


def test_revert_twin_drops_in_dependency_order():
    revert = render_mod.render_bundle(BARIQ).revert_sql
    pos = {token: revert.index(token) for token in (
        "DROP POLICY IF EXISTS", "DROP TABLE IF EXISTS", "DROP ROLE IF EXISTS",
        "DELETE FROM organizations",
    )}
    assert pos["DROP POLICY IF EXISTS"] < pos["DROP TABLE IF EXISTS"] < \
           pos["DROP ROLE IF EXISTS"] < pos["DELETE FROM organizations"], pos
    assert f"DELETE FROM organizations WHERE id = '{BARIQ.org_uuid}'" in revert


def test_bundle_sha_is_of_the_forward_text_and_stable():
    b1, b2 = render_mod.render_bundle(BARIQ), render_mod.render_bundle(BARIQ)
    assert b1.sha256 == b2.sha256
    assert b1.sha256 == hashlib.sha256(b1.forward_sql.encode()).hexdigest()


def test_renderer_refuses_an_unpinned_policy_template(monkeypatch):
    # the structural org-pinning check (plan §4.3) fires on template drift:
    # a hand-edited template adding a policy WITHOUT the org pin must Refuse
    monkeypatch.setattr(render_mod, "_POLICY_TEMPLATES", list(render_mod._POLICY_TEMPLATES) + [
        ("{content_table}_rogue_read", "ON {content_table} FOR SELECT TO anon USING (true)"),
    ])
    with pytest.raises(Refuse):
        render_mod.render_bundle(BARIQ)


def test_audit_function_execute_revoked_from_public_too():
    # cc-quality Minor a (PR#566): function EXECUTE defaults to PUBLIC, and
    # revoking anon/authenticated alone leaves the anon-reachable path open
    # per the standing SECDEF rule — the fn is trigger-only today, but the
    # lockdown must not depend on reachability
    out = render_mod.render_bundle(BARIQ)
    assert (f"REVOKE ALL ON FUNCTION {BARIQ.content_table}_audit() "
            "FROM anon, authenticated, PUBLIC;") in out.forward_sql


def test_spec_rejects_hyphenated_client_keys():
    # cc-quality Minor b: a hyphen renders invalid unquoted DDL — refuse at
    # validation, not at apply-time parse (identifier-safe set: lower + digit
    # + underscore, leading letter)
    from provisioner.spec import ClientSpec
    base = dict(
        client_key="bad-key",
        alias="ihsanos multi-tenant DB",
        org_uuid="6261642d-7100-4000-8000-000000000001",  # hex("bad-") — shape-valid
        org_name="Bad Hyphen Pte. Ltd.",
        org_uen="202219999Z",
        org_slug="bad-hyphen",
        seed_keys=["site"],
    )
    with pytest.raises(Refuse):
        ClientSpec.from_dict(base)
