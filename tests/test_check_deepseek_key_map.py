"""check_deepseek_key_map.py (orch-console bus #84011): every vault key name
deepseek_key_map.json references must exist in vault_secrets, or the gap
stays invisible until a lane actually tries to boot under that family
(exactly what happened to hc95/fleet/loop before this check existed).

DB access is mocked (monkeypatched psycopg.connect) so this never touches
the real vault_secrets table or depends on its current contents -- the
point is to test the SCRIPT's own logic, not today's live vault state.
"""
import importlib.util
import json
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "check_deepseek_key_map.py")

_spec = importlib.util.spec_from_file_location("check_deepseek_key_map", _SCRIPT)
checker = importlib.util.module_from_spec(_spec)
sys.modules["check_deepseek_key_map"] = checker
_spec.loader.exec_module(checker)


class _FakeCursor:
    def __init__(self, present_names):
        self._present_names = present_names
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        named = set(params[0])
        self._result = [(n,) for n in named & self._present_names]

    def fetchall(self):
        return self._result


class _FakeConn:
    def __init__(self, present_names):
        self._present_names = present_names

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self._present_names)


def _install_fake_vault(monkeypatch, present_names):
    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = lambda dsn: _FakeConn(set(present_names))
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")


def test_no_dangling_keys_when_every_mapped_key_exists(monkeypatch):
    with open(checker._MAP_FILE) as f:
        mapping = json.load(f)
    _install_fake_vault(monkeypatch, present_names=set(mapping.values()))
    assert checker.dangling_vault_keys() == []
    assert checker.main([]) == 0


def test_reports_a_dangling_key_not_in_the_vault(monkeypatch):
    _install_fake_vault(monkeypatch, present_names=set())  # nothing exists
    with open(checker._MAP_FILE) as f:
        mapping = json.load(f)
    missing = checker.dangling_vault_keys()
    assert missing == sorted(set(mapping.values()))
    assert checker.main([]) == 1


def test_main_exits_nonzero_and_fails_loud_on_db_error(monkeypatch, capsys):
    def _boom(dsn):
        raise RuntimeError("connection refused (simulated)")
    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = _boom
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    rc = checker.main([])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAILED to check" in err


def test_alert_flag_pages_orch_console_on_a_dangling_key(monkeypatch):
    _install_fake_vault(monkeypatch, present_names=set())
    sent = {}

    def _fake_send(**kwargs):
        sent.update(kwargs)

    import scripts.bus_send as bus_send_mod
    monkeypatch.setattr(bus_send_mod, "send", _fake_send)

    rc = checker.main(["--alert"])
    assert rc == 1
    assert sent["to"] == "orch-console"
    assert sent["from_agent"] == "cc-fleet-health"
    assert "deepseek_key_map.json" in sent["body"]
