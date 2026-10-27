"""Console R4 armed apply: enabled on MUSA's verified word, never a cai sign-off (op#34916).

The R4 endpoints (/api/apply-armed, /api/governance-set, and the queued-apply
watcher) used to answer 503 "pending cai sign-off + Nazim review" until someone
set CONSOLE_R4_ENABLED=1. Musa op#34916: cai advises and never blocks. R4 now
enables on EITHER the existing env switch OR a bridge-verified Musa Telegram
"ENABLE LANE APPLY" inside 24h (require_verified_authorization). Every per-apply
safety check (typed confirm, gazzabyte fail-closed, one switch rail) is untouched.
"""
import pathlib

import pytest

from nervous_system.console import app

SRC = pathlib.Path(app.__file__).read_text()


class _Auth:
    def __init__(self, ok, reason="", row=None):
        self.ok, self.reason, self.row = ok, reason, row


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("CONSOLE_R4_ENABLED", raising=False)
    app._R4_ENABLE_CACHE.clear()
    yield
    app._R4_ENABLE_CACHE.clear()


def test_env_switch_alone_enables(monkeypatch):
    monkeypatch.setenv("CONSOLE_R4_ENABLED", "1")
    ok, why = app._r4_enabled(verify=lambda *a, **k: _Auth(False, "none"))
    assert ok is True and "CONSOLE_R4_ENABLED" in why


def test_musa_verified_enable_alone_enables():
    seen = {}
    def verify(op_id, **kw):
        seen.update(op_id=op_id, **kw)
        return _Auth(True, "ok", {"id": 9001})
    ok, why = app._r4_enabled(verify=verify)
    assert ok is True and "9001" in why
    assert seen["op_id"] == "console-r4-enable"
    assert app._R4_ENABLE_PHRASE in seen["approval_phrases"]


def test_neither_means_disabled_and_names_musa_not_cai():
    ok, why = app._r4_enabled(verify=lambda *a, **k: _Auth(False, "no matching row"))
    assert ok is False and "no matching row" in why


def test_verifier_error_fails_closed():
    def boom(*a, **k):
        raise RuntimeError("db down")
    ok, why = app._r4_enabled(verify=boom)
    assert ok is False and "RuntimeError" in why


def test_result_is_cached_so_the_queue_watcher_does_not_hammer_the_db():
    calls = {"n": 0}
    def verify(*a, **k):
        calls["n"] += 1
        return _Auth(True, "ok", {"id": 1})
    app._r4_enabled(verify=verify)
    app._r4_enabled(verify=verify)
    assert calls["n"] == 1


def test_every_r4_gate_uses_the_helper_and_no_cai_sign_off_remains():
    assert "pending cai sign-off" not in SRC
    assert 'enabled = os.environ.get("CONSOLE_R4_ENABLED") == "1"' not in SRC
    assert "if not _R4_ENABLED:" not in SRC
    assert SRC.count("_r4_enabled()") >= 3
