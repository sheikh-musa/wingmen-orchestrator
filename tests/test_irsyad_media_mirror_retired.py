"""The irsyad media mirror must never scp from a RETIRED hub host (op37138 / console 89343).

2026-11-01: wingmen-core is decommissioned AND its host key has since changed, so an scp there
may land on a DIFFERENT machine. The mirror's resolver used to hand back the hardcoded
`root@91.107.235.77` target whenever the lease named wingmen-core — a second copy of the
address hub_reach now refuses. It must resolve to None instead (the caller's logged skip
path): refusal by name, never a silent wrong-host copy of a client's media.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts import irsyad_media_mirror as mirror  # noqa: E402

WINGMEN_CORE_IP = "91.107.235.77"


def _with_holder(monkeypatch, holder):
    monkeypatch.delenv("WINGMEN_VPS_HOST", raising=False)
    monkeypatch.setattr(mirror.hub_reach, "read_holder_host", lambda conn: holder)
    return mirror._resolve_vps_host(conn=None)


def test_retired_holder_resolves_to_no_scp_target(monkeypatch):
    for holder in ("wingmen-core", "hermes-vps", WINGMEN_CORE_IP, "  WINGMEN-CORE  "):
        target = _with_holder(monkeypatch, holder)
        assert target is None, f"{holder!r} must not resolve to a scp target"
        assert target != WINGMEN_CORE_IP


def test_live_gzb_holder_still_resolves_to_no_target_here(monkeypatch):
    # gzb's layout is NOT implemented by this script (different user/home dir) — that stays
    # None, unchanged by the retirement; the point is only that it is never the dead IP.
    assert _with_holder(monkeypatch, "gzbai") is None


def test_unknown_holder_is_never_guessed(monkeypatch):
    assert _with_holder(monkeypatch, None) is None
    assert _with_holder(monkeypatch, "some-new-host-99") is None


def test_explicit_operator_override_still_wins(monkeypatch):
    # WINGMEN_VPS_HOST is an explicit operator act, not a holder-derived guess; the real
    # fail-closed gate for a mutating/insecure path stays in that path itself.
    monkeypatch.setenv("WINGMEN_VPS_HOST", "some-explicit-host")
    monkeypatch.setattr(mirror.hub_reach, "read_holder_host", lambda conn: "wingmen-core")
    assert mirror._resolve_vps_host(conn=None) == "some-explicit-host"
