"""ClientSpec — the per-client parameterization of what provision_bariq.py
hardcoded (role, tables, org UUID, seed keys). Plan §0/§1: the bundle
renders FROM A SPEC, and the spec validates fail-closed before anything
renders.
"""
import json
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import spec as spec_mod  # noqa: E402
from provisioner.registry import Refuse  # noqa: E402


def _spec_dict(**overrides):
    base = dict(
        client_key="acme",
        alias="ihsanos multi-tenant DB",
        org_uuid="61636d65-7100-4000-8000-000000000001",  # hex("acme") prefix
        seed_keys=["site", "hero"],
    )
    base.update(overrides)
    return base


def test_spec_validates_and_derives_names():
    s = spec_mod.ClientSpec.from_dict(_spec_dict())
    assert s.client_key == "acme"
    # derived, never spec-carried: the names a client can't cross-wire
    assert s.editor_role == "acme_editor"
    assert s.content_table == "acme_site_content"
    assert s.history_table == "acme_site_content_history"


def test_spec_org_uuid_is_the_lookup_free_pin():
    # the bariq migration's documented convention: the first 8 hex chars ARE
    # the first 4 chars of the client key — the org row is recognizable
    # without a lookup. Wrong prefix must Refuse.
    good = spec_mod.ClientSpec.from_dict(_spec_dict())
    assert good.org_uuid.startswith("acme".encode().hex())
    bad_uuid = "62617269-7100-4000-8000-000000000001"  # "bari" pin under acme
    with pytest.raises(Refuse):
        spec_mod.ClientSpec.from_dict(_spec_dict(org_uuid=bad_uuid))


def test_spec_alias_must_resolve_in_the_registry():
    # a spec naming a store that doesn't exist refuses at VALIDATION time,
    # not at apply time (the ref is resolved via registry.store_ref)
    with pytest.raises(Refuse):
        spec_mod.ClientSpec.from_dict(_spec_dict(alias="no such store"))


def test_spec_slug_and_shape_rules():
    with pytest.raises(Refuse):  # not a slug
        spec_mod.ClientSpec.from_dict(_spec_dict(client_key="Bad_Key"))
    with pytest.raises(Refuse):  # shorter than the 4-char hex-prefix pin
        spec_mod.ClientSpec.from_dict(_spec_dict(client_key="abc"))
    with pytest.raises(Refuse):  # not a UUID
        spec_mod.ClientSpec.from_dict(_spec_dict(org_uuid="not-a-uuid"))
    with pytest.raises(Refuse):  # no seed keys -> nothing to verify against
        spec_mod.ClientSpec.from_dict(_spec_dict(seed_keys=[]))


def test_bariq_spec_file_loads():
    # the reference client: the spec file reproduces the LIVE bariq org row
    # (62617269 = hex "bari", migration documented as a lookup-free pin)
    s = spec_mod.load_spec(ORCH_DIR / "scripts" / "lib" / "provisioner" / "specs" / "bariq.json")
    assert s.client_key == "bariq"
    assert s.org_uuid == "62617269" + "-7100-4000-8000-000000000001"
    assert s.org_uuid.startswith("bari".encode().hex())
    assert s.content_table == "bariq_site_content"


def test_load_spec_refuses_garbage(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(Refuse):
        spec_mod.load_spec(missing)
    malformed = tmp_path / "bad.json"
    malformed.write_text("{not json")
    with pytest.raises(Refuse):
        spec_mod.load_spec(malformed)
    wrong_client = tmp_path / "wrong.json"
    wrong_client.write_text(json.dumps(_spec_dict(client_key="acme")))
    with pytest.raises(Refuse):  # filename must match client_key
        spec_mod.load_spec(wrong_client)
