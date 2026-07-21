"""Tests for the ihsan_gate reader (Head of Quality, Phase 1 — CODIFY).

Verifies the manifest parses + is internally consistent, the reader maps a
change class -> required gate items, unknown classes fall back to the strictest
default, and the module is a pure read-only projection (zero side effects).
"""

import copy

import pytest

from nervous_system import ihsan_gate


# --------------------------------------------------------------------------- #
# Manifest integrity
# --------------------------------------------------------------------------- #

def test_manifest_parses():
    m = ihsan_gate.load_manifest()
    assert isinstance(m, dict)
    assert isinstance(m["v"], int)
    assert m["v"] >= 1
    assert m["gate_items"]
    assert m["change_classes"]


def test_manifest_is_internally_consistent():
    # Every referenced gate item is defined; default_class exists; types valid.
    errors = ihsan_gate.validate_manifest()
    assert errors == [], f"manifest inconsistencies: {errors}"


def test_every_gate_item_has_valid_type():
    items = ihsan_gate.list_gate_items()
    assert set(items) >= {f"G{i}" for i in range(1, 11)}
    for iid, spec in items.items():
        assert spec["type"] in ("deterministic", "judgment"), iid


def test_enforcement_is_none_phase1():
    # The Phase-1 contract, asserted in data: this manifest enforces nothing.
    m = ihsan_gate.load_manifest()
    assert m["meta"]["enforcement"] == "none"
    assert m["meta"]["phase"] == 1


# --------------------------------------------------------------------------- #
# change-class -> required items
# --------------------------------------------------------------------------- #

def test_docs_class_minimal_items():
    ids = ihsan_gate.required_item_ids("docs-copy")
    assert ids == ["G1", "G7", "G10"]


def test_docs_class_via_alias():
    # 'docs/copy', 'copy', 'comment' all normalize to docs-copy.
    assert ihsan_gate.resolve_class("docs/copy") == "docs-copy"
    assert ihsan_gate.resolve_class("copy") == "docs-copy"
    assert ihsan_gate.resolve_class(" Comment ") == "docs-copy"


def test_ui_class_pulls_design_pipeline():
    ids = ihsan_gate.required_item_ids("ui-frontend")
    assert {"G2", "G3", "G9"} <= set(ids)
    # aliased forms resolve too
    assert ihsan_gate.resolve_class("UI/frontend") == "ui-frontend"


def test_money_and_pii_pull_full_floor():
    all_items = set(ihsan_gate.list_gate_items())
    for cls in ("money-payment", "pii-gov-data", "deploy-prod", "deploy-client"):
        ids = set(ihsan_gate.required_item_ids(cls))
        assert ids == all_items, f"{cls} should require the full floor"
        # G5 (security) is a mandatory reviewer arm for these classes
        m = ihsan_gate.load_manifest()
        assert "G5" in m["change_classes"][cls]["mandatory_reviews"]
        assert m["change_classes"][cls]["cai_gate"] is True


def test_gate_items_for_returns_enriched_dicts():
    items = ihsan_gate.gate_items_for("db-migration")
    ids = [i["id"] for i in items]
    assert ids == ihsan_gate.required_item_ids("db-migration")
    for i in items:
        assert i["resolved_class"] == "db-migration"
        assert "title" in i and "type" in i


def test_deterministic_and_judgment_split():
    det = ihsan_gate.deterministic_items("ui-frontend")
    jud = ihsan_gate.judgment_items("ui-frontend")
    det_ids = {i["id"] for i in det}
    jud_ids = {i["id"] for i in jud}
    assert det_ids.isdisjoint(jud_ids)
    # G9 (ihsan polish) is a judgment item; G1 (CI) is deterministic.
    assert "G9" in jud_ids
    assert "G1" in det_ids


# --------------------------------------------------------------------------- #
# Safe default for unknown / ambiguous class
# --------------------------------------------------------------------------- #

def test_unknown_class_falls_back_to_strictest_default():
    m = ihsan_gate.load_manifest()
    default = m["default_class"]
    assert ihsan_gate.resolve_class("totally-made-up-class") == default
    # unknown class gets the FULL floor (strictest), not an empty/lenient set
    unknown_ids = set(ihsan_gate.required_item_ids("totally-made-up-class"))
    assert unknown_ids == set(ihsan_gate.list_gate_items())


def test_none_class_falls_back_to_default():
    m = ihsan_gate.load_manifest()
    assert ihsan_gate.resolve_class(None) == m["default_class"]


# --------------------------------------------------------------------------- #
# Purity: read-only, no side effects
# --------------------------------------------------------------------------- #

def test_reader_does_not_mutate_manifest():
    m = ihsan_gate.load_manifest()
    before = copy.deepcopy(m)
    items = ihsan_gate.gate_items_for("money-payment")
    items[0]["title"] = "MUTATED"  # mutate the returned copy
    # source manifest untouched
    assert m == before


def test_validate_catches_dangling_reference():
    m = copy.deepcopy(ihsan_gate.load_manifest())
    m["change_classes"]["docs-copy"]["required_items"].append("G999")
    errors = ihsan_gate.validate_manifest(m)
    assert any("G999" in e for e in errors)


def test_validate_catches_bad_default_class():
    m = copy.deepcopy(ihsan_gate.load_manifest())
    m["default_class"] = "no-such-class"
    errors = ihsan_gate.validate_manifest(m)
    assert any("default_class" in e for e in errors)
