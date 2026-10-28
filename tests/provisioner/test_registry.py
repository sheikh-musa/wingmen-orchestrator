"""Finding-1 pin (op#78463): the store ref is LOADED from the registry,
never hand-typed. The plan's first draft hand-typed the ceayj ref wrong
seven times (z at char 11); these tests make that class of error
structurally impossible to reintroduce.
"""
import re
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import registry  # noqa: E402

CEAYJ_ALIAS = "ihsanos multi-tenant DB"

# The error being pinned (plan §8): z at character 11. Spelled here ONLY as
# the negative citation, never as a value the code could pick up.
_Z_VARIANT = "ceayjeamtmzyzzvqflus"


def _registry_row_ref(alias):
    """Independent parse of the SAME doc the loader reads — the round-trip
    assert compares two independent readers, never loader vs a hand-typed
    constant."""
    text = registry.REGISTRY_DOC.read_text()
    m = re.search(rf"^\| \*\*{re.escape(alias)}\*\* \| `([a-z0-9]{{20}})` \|", text, re.MULTILINE)
    assert m, f"registry doc lost its {alias!r} row"
    return m.group(1)


def test_store_ref_matches_the_registry_row_round_trip():
    assert registry.store_ref(CEAYJ_ALIAS) == _registry_row_ref(CEAYJ_ALIAS)


def test_store_ref_shape_and_not_the_z_variant():
    ref = registry.store_ref(CEAYJ_ALIAS)
    assert re.fullmatch(r"[a-z0-9]{20}", ref)
    assert ref != _Z_VARIANT
    assert ref[10] == "c"  # character 11 (1-indexed): the z-variant's tell


def test_store_ref_refuses_unknown_alias():
    with pytest.raises(registry.Refuse):
        registry.store_ref("no such store alias")


def test_no_ref_literal_in_the_provisioner_package():
    """The registry doc is the ONLY place a store ref is ever spelled: no
    module under scripts/lib/provisioner/ may carry the registry's ref as a
    literal (finding 1's structural half)."""
    ref = _registry_row_ref(CEAYJ_ALIAS)
    for py in (ORCH_DIR / "scripts" / "lib" / "provisioner").glob("*.py"):
        assert ref not in py.read_text(), f"{py.name} hardcodes the store ref"
