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
    forward = render_mod.render_bundle(BARIQ).forward_sql
    policies = re.findall(r"^CREATE POLICY .*?(?=^\s*(?:DROP|CREATE|REVOKE|GRANT|ALTER|INSERT|--)|\Z)",
                          forward, re.MULTILINE | re.DOTALL)
    assert len(policies) >= 4  # anon read + editor select/update + history select
    for p in policies:
        assert f"org_id = '{BARIQ.org_uuid}'" in p, f"unpinned policy:\n{p}"


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
