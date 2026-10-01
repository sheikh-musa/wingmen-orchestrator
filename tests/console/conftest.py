"""tests/console shared fixtures.

op#24597: /api/fleet now fans out to glm_usage.get_glm_usage(), which reads the
GLM key from the fleet VAULT (a live substrate connection) and calls z.ai. No
console test may touch either, so by default the key reader and the HTTP call
are replaced with fail-fast stubs and the module cache is cleared per test.
Tests that exercise the GLM card install their own fake key / mocked HTTP.
"""
import pytest

from nervous_system.console import glm_usage


def _no_vault():
    raise glm_usage.GlmUsageError("vault disabled in tests")


def _no_http(url, key):
    raise glm_usage.GlmUsageError("network disabled in tests")


@pytest.fixture(autouse=True)
def _hermetic_glm_usage(monkeypatch):
    monkeypatch.setattr(glm_usage, "read_key", _no_vault)
    monkeypatch.setattr(glm_usage, "http_get_json", _no_http)
    glm_usage._reset_for_tests()
    yield
    glm_usage._reset_for_tests()
