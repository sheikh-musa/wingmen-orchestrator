"""op#24342 category (c): the *_bus_notify pollers resolve DATABASE_URL file-first.

Wet-proves the SHIPPED code path: import each real module and call its real _dsn()
with a STALE os.environ DATABASE_URL + a different .env FILE value, asserting the FILE
value wins (so a pre-rotation poller can't hammer the pooler). Also proves each module
imports cleanly (the `from scripts.lib.substrate_dsn import dsn_from_env_file` resolves).
"""
import importlib
import os

import pytest

MODULES = [
    "nervous_system.cai_bus_notify",
    "nervous_system.nazim_bus_notify",
    "nervous_system.finance_bus_notify",
    "nervous_system.fleet_health_bus_notify",
]

FILE_DSN = "postgresql://file-user:file-pw@aws-pooler:5432/postgres"
STALE_DSN = "postgresql://STALE:STALE@aws-pooler:5432/postgres"


@pytest.mark.parametrize("modname", MODULES)
def test_bus_notify_dsn_is_file_first(modname, tmp_path, monkeypatch):
    mod = importlib.import_module(modname)  # also proves it imports (scripts.lib resolves)
    (tmp_path / ".env").write_text("DATABASE_URL=%s\n" % FILE_DSN)
    monkeypatch.setattr(mod, "ORCH_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", STALE_DSN)  # stale inherited value must NOT win
    assert mod._dsn() == FILE_DSN
