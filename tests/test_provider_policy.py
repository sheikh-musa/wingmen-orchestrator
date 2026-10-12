"""Tests for the provider_policy resolver (Musa op#29321/#29324).

The resolver answers "for this client_key, which model providers are allowed,
and is text_only (vision/image MCP off) required?" from provider_policy rows.
Core safety property: fail-closed — an unknown key, or a row that allows a
non-Anthropic provider without text_only, resolves to Anthropic-only.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lib"))
import provider_policy as pp  # noqa: E402


def test_unknown_client_fails_closed_to_anthropic_only():
    # No matching row must NEVER default to "safe to use a non-Anthropic provider".
    result = pp.resolve_policy(rows=[], client_key="totally-unknown")
    assert result == {"allowed_providers": ["anthropic"], "text_only": False}


def test_zai_without_text_only_is_refused_fail_closed():
    # Musa's HARD condition (op#29321/#29324): a non-Anthropic provider is allowed
    # only WITH text_only=true (vision/image MCP off, the 10-07 image-store finding).
    # A row that allows zai but forgets text_only must NOT silently route to z.ai —
    # it fails closed to Anthropic-only rather than leaking images to z.ai.
    rows = [{"client_key": "misconfigured", "allowed_providers": ["anthropic", "zai"], "text_only": False}]
    result = pp.resolve_policy(rows=rows, client_key="misconfigured")
    assert result == {"allowed_providers": ["anthropic"], "text_only": False}


def test_irsyad_is_anthropic_only():
    # Musa op#29321: irsyad = anthropic-only, HARD. No z.ai, ever.
    rows = [{"client_key": "irsyad", "allowed_providers": ["anthropic"], "text_only": False}]
    result = pp.resolve_policy(rows=rows, client_key="irsyad")
    assert result == {"allowed_providers": ["anthropic"], "text_only": False}


def test_cosem_adcda_is_glm_allowed_text_only():
    # Musa op#29324: ADCDA allowed on GLM, text_only=true (data via Singapore).
    rows = [{"client_key": "cosem-adcda", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    result = pp.resolve_policy(rows=rows, client_key="cosem-adcda")
    assert result == {"allowed_providers": ["anthropic", "zai"], "text_only": True}
