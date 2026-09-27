"""Durability + safety guards for the DDL-coverage-watchdog launchd config (bus #44181/#44191).

These plists + wrapper are committed so the Mini's config survives a disk loss. The guards:
  * NO secret in the committed plist (the wrapper exists precisely so the DSN never lands in a
    world-readable ~/Library/LaunchAgents file — bus #44135/#44153);
  * each plist's ProgramArguments points at the in-repo wrapper (so a restore-from-git is
    coherent — the plist and the script it runs come from the same checkout);
  * label matches the filename, cadence is the expected 300s.
"""
import plistlib
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_WRAPPER_REL = "scripts/run_ddl_coverage_watchdog.sh"
_PLISTS = [
    _REPO / "launchd" / "dev.wingmen.ddl-coverage-watchdog-substrate.plist",
    _REPO / "launchd" / "dev.wingmen.ddl-coverage-watchdog-ywrpt.plist",
]
# crude but effective secret smells for a world-readable file
_SECRET_RE = re.compile(r"postgres(?:ql)?://|password\s*=|service_role|SUPABASE_SERVICE|eyJ[A-Za-z0-9_-]{20}")


@pytest.mark.parametrize("plist", _PLISTS, ids=lambda p: p.name)
def test_plist_carries_no_secret(plist):
    text = plist.read_text()
    m = _SECRET_RE.search(text)
    assert m is None, f"{plist.name} appears to embed a secret ({m.group(0)!r}) — DSN must stay in the wrapper/.env"


@pytest.mark.parametrize("plist", _PLISTS, ids=lambda p: p.name)
def test_plist_runs_the_in_repo_wrapper(plist):
    with plist.open("rb") as fh:
        d = plistlib.load(fh)
    args = d["ProgramArguments"]
    assert args[0].endswith(_WRAPPER_REL), f"{plist.name} ProgramArguments[0]={args[0]} must run {_WRAPPER_REL}"
    assert (_REPO / _WRAPPER_REL).is_file(), "the committed wrapper the plist references is missing from the repo"
    assert args[1] in ("substrate", "cosem")
    assert d["Label"] == plist.stem                 # label == filename (launchctl consistency)
    assert d["StartInterval"] == 300                # 5-min cadence (op#22669 item 3)


def test_wrapper_embeds_no_dsn():
    src = (_REPO / _WRAPPER_REL).read_text()
    # the wrapper sources .env / uses a vault key — it must never inline a DSN
    assert "postgres://" not in src and "postgresql://" not in src
    assert 'set -a; . ./.env; set +a' in src        # DSN comes from .env, not the source


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
