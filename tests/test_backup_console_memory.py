"""backup_console_memory.py must derive MEMORY_DIR from $HOME (audit B#1).

Regression: the script hardcoded ``~/.claude/projects/-Users-musa-wingmen-orchestrator``,
a path that does not exist on this Mac (HOME is /Users/sheikhmusa), so the nightly
snapshot silently stopped on 2026-07-11. Pure unit test — no DB.
"""
import importlib
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")


def _load(monkeypatch, home, override=None):
    monkeypatch.setenv("HOME", home)
    if override is None:
        monkeypatch.delenv("CONSOLE_MEMORY_DIR", raising=False)
    else:
        monkeypatch.setenv("CONSOLE_MEMORY_DIR", override)
    monkeypatch.syspath_prepend(_SCRIPTS)
    sys.modules.pop("backup_console_memory", None)
    return importlib.import_module("backup_console_memory")


@pytest.mark.parametrize("home", ["/Users/sheikhmusa", "/Users/musa", "/home/wingmen"])
def test_memory_dir_derives_from_home(monkeypatch, home):
    mod = _load(monkeypatch, home)
    slug = f"{home}/wingmen/orchestrator".replace("/", "-")
    assert mod.MEMORY_DIR == f"{home}/.claude/projects/{slug}/memory"
    assert "Users-musa" not in mod.MEMORY_DIR or home == "/Users/musa"


def test_memory_dir_env_override_wins(monkeypatch, tmp_path):
    mod = _load(monkeypatch, "/Users/sheikhmusa", override=str(tmp_path))
    assert mod.MEMORY_DIR == str(tmp_path)


def test_source_has_no_hardcoded_user_path():
    src = open(os.path.join(_SCRIPTS, "backup_console_memory.py"), encoding="utf-8").read()
    assert "-Users-musa-" not in src
    assert "/Users/musa" not in src
