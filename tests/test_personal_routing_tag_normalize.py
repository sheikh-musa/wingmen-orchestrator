"""cc-quality PR #240 LOW: is_personal_routed() must fail SAFE (still route)
on a case/whitespace variant of a routed tag, not fail open to the
substrate."""
import importlib

personal_routing = importlib.import_module("nervous_system.personal_routing")


def test_exact_tag_routes():
    assert personal_routing.is_personal_routed("mamadah") is True


def test_uppercase_variant_still_routes():
    assert personal_routing.is_personal_routed("MAMADAH") is True


def test_whitespace_variant_still_routes():
    assert personal_routing.is_personal_routed(" mamadah ") is True


def test_mixed_case_and_whitespace_still_routes():
    assert personal_routing.is_personal_routed("  Mamadah\n") is True


def test_unrelated_tag_does_not_route():
    assert personal_routing.is_personal_routed("oeh") is False


def test_none_tag_does_not_route():
    assert personal_routing.is_personal_routed(None) is False


def test_empty_tag_does_not_route():
    assert personal_routing.is_personal_routed("") is False
