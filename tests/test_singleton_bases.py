"""singleton_bases derives the singleton set from the protected_agents registry (kind
'*-singleton'), never a hardcoded list (orch-console #77922; bus #42909)."""
import pytest

from nervous_system import protected_agents as pa
from scripts.lib import singleton_bases as sb


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(sb, "_cache", {"at": 0.0, "ids": None})


def _row(aid, kind):
    return pa.ProtectedAgent(aid, kind, None, False, "r")


def test_singletons_are_the_registry_rows_whose_kind_is_a_singleton(monkeypatch):
    monkeypatch.setattr(pa, "_safe_registry_rows", lambda dsn=None: [
        _row("cc-orchestrator", "always-on-singleton"), _row("cc-quality", "auditor-singleton"),
        _row("nazim-console", None), _row("cc-x", "worker")])
    assert sb.singleton_base_ids() == {"cc-orchestrator", "cc-quality"}
    assert sb.is_singleton_base("cc-orchestrator") and not sb.is_singleton_base("cc-x")


def test_untrusted_registry_read_falls_back_to_the_static_floor(monkeypatch):
    monkeypatch.setattr(pa, "_safe_registry_rows", lambda dsn=None: None)
    monkeypatch.setattr(pa, "protected_agent_ids", lambda dsn=None: frozenset({"cc-orchestrator"}))
    assert sb.singleton_base_ids() == {"cc-orchestrator"}


def test_result_is_cached_for_the_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(pa, "_safe_registry_rows",
                        lambda dsn=None: calls.append(1) or [_row("cai", "always-on-singleton")])
    sb.singleton_base_ids(now=1000)
    sb.singleton_base_ids(now=1000 + sb._TTL_S - 1)
    assert len(calls) == 1
    sb.singleton_base_ids(now=1000 + sb._TTL_S + 1)
    assert len(calls) == 2


def test_empty_id_is_never_a_singleton():
    assert sb.is_singleton_base(None) is False and sb.is_singleton_base("") is False
