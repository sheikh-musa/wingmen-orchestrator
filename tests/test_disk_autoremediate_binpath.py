"""Regression: disk_autoremediate must resolve npm/brew by ABSOLUTE path (bus #44270).

The critical-tier self-heal ran but `npm cache clean` / `brew cleanup` failed with
"[Errno 2] No such file or directory: 'npm'/'brew'": the reclaimers used bare argv
(`["npm", ...]`, `["brew", ...]`) and the launchd/agent PATH lacks /usr/local/bin
and /opt/homebrew/bin where those tools live. So at the tier that matters most the
reclaimers silently no-op'd — a dead-man's-switch hole. The fix resolves an absolute
path (which() first, then known bin dirs) and, when a tool is genuinely absent,
SKIPS that reclaimer loudly instead of raising an FileNotFound at run time.
"""
import importlib.util
import os
import pathlib

import pytest

_MOD = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "disk_autoremediate.py"
_spec = importlib.util.spec_from_file_location("disk_autoremediate", _MOD)
da = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(da)


def test_resolve_bin_uses_which_first(monkeypatch):
    monkeypatch.setattr(da.shutil, "which", lambda name: f"/from/which/{name}")
    assert da._resolve_bin("npm") == "/from/which/npm"


def test_resolve_bin_falls_back_to_known_dirs(tmp_path, monkeypatch):
    # which() misses (minimal launchd PATH) but the tool exists in a known bin dir.
    fake = tmp_path / "npm"
    fake.write_text("#!/bin/sh\n")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(da.shutil, "which", lambda name: None)
    monkeypatch.setattr(da, "_BIN_DIRS", [str(tmp_path)])
    assert da._resolve_bin("npm") == str(fake)


def test_resolve_bin_none_when_absent(monkeypatch):
    monkeypatch.setattr(da.shutil, "which", lambda name: None)
    monkeypatch.setattr(da, "_BIN_DIRS", ["/nonexistent-bin-dir"])
    assert da._resolve_bin("brew") is None


def _names(reclaimers):
    return [r["name"] for r in reclaimers]


def test_build_reclaimers_skips_a_missing_tool_loudly(monkeypatch):
    # npm present, brew absent → brew-cleanup is skipped (not added), npm-cache stays.
    monkeypatch.setattr(da, "_resolve_bin",
                        lambda name: "/usr/local/bin/npm" if name == "npm" else None)
    names = _names(da.build_reclaimers())
    assert "npm-cache" in names
    assert "brew-cleanup" not in names          # skipped, so it can't FileNotFound at run time
    assert "file-history" in names and "next-cache" in names


def test_build_reclaimers_uses_absolute_argv(monkeypatch):
    monkeypatch.setattr(da, "_resolve_bin", lambda name: f"/opt/homebrew/bin/{name}")
    calls = []
    # brew --cache is probed during build; capture it and any reclaimer subprocess.
    monkeypatch.setattr(da.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or type("R", (), {"stdout": "/brew/cache", "returncode": 0})())
    recs = {r["name"]: r for r in da.build_reclaimers()}
    # the brew --cache probe used the absolute brew path
    assert any(c[0] == "/opt/homebrew/bin/brew" for c in calls), calls
    # and the npm reclaimer, when applied, shells the absolute npm path
    monkeypatch.setattr(da, "_dir_size", lambda p: 0)
    recs["npm-cache"]["apply"](dry_run=False)
    assert calls[-1][0] == "/opt/homebrew/bin/npm", calls[-1]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
