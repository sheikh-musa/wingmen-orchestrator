"""P0 #46618 hardening: (2) fleet_host_id refuses a foreign pin, (3) orch_lease.decide
fails CLOSED for a non-holder on an EXPIRED lease (no silent pen-gate no-op), (4) a
renew failure PAGES. All DB-free / mockable — no live substrate, safe with DATABASE_URL
unset (the prod-ref conftest guard, PR #223, then allows the session)."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.lib import fleet_host_id, orch_lease  # noqa: E402

_MAP = {"gzbai": ["gzbai", "gzbai.local"],
        "Sheikhs-Mini": ["Sheikhs-Mini", "Sheikhs-Mac-mini"]}


# ── (2) fleet_host_id: refuse a foreign pin ──────────────────────────────────
def _patch_host(monkeypatch, live):
    monkeypatch.setattr(fleet_host_id.socket, "gethostname", lambda: live)
    monkeypatch.setattr(fleet_host_id, "_load_map", lambda: _MAP)


def test_foreign_pin_raises(monkeypatch):
    _patch_host(monkeypatch, "gzbai")
    monkeypatch.setenv("FLEET_HOST_ID", "Sheikhs-Mini")   # the P0: Mini's id on gzb
    with pytest.raises(fleet_host_id.ForeignHostPinError):
        fleet_host_id.fleet_host_id()


def test_local_canonical_pin_honored(monkeypatch):
    _patch_host(monkeypatch, "gzbai")
    monkeypatch.setenv("FLEET_HOST_ID", "gzbai")
    assert fleet_host_id.fleet_host_id() == "gzbai"


def test_local_alias_pin_honored(monkeypatch):
    _patch_host(monkeypatch, "gzbai")
    monkeypatch.setenv("FLEET_HOST_ID", "gzbai.local")    # an alias of this host
    assert fleet_host_id.fleet_host_id() == "gzbai.local"


def test_unset_falls_to_alias_match(monkeypatch):
    _patch_host(monkeypatch, "gzbai.local")               # a flapped variant
    monkeypatch.delenv("FLEET_HOST_ID", raising=False)
    assert fleet_host_id.fleet_host_id() == "gzbai"       # canonical via alias-match


def test_unmapped_host_pin_must_match_raw(monkeypatch):
    _patch_host(monkeypatch, "brand-new-box")             # not in the map
    monkeypatch.setenv("FLEET_HOST_ID", "brand-new-box")  # matches raw hostname -> ok
    assert fleet_host_id.fleet_host_id() == "brand-new-box"
    monkeypatch.setenv("FLEET_HOST_ID", "some-other-host")
    with pytest.raises(fleet_host_id.ForeignHostPinError):
        fleet_host_id.fleet_host_id()


# ── (3) decide: fail-closed for a non-holder on an EXPIRED lease ──────────────
def _row(holder_host, renewed_at, ttl=900, holder="cc-orchestrator"):
    return {"holder": holder, "holder_host": holder_host,
            "renewed_at": renewed_at, "ttl_seconds": ttl}


def test_decide_current_holder_ok():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    ok, why = orch_lease.decide(_row("gzbai", now), "gzbai", now)
    assert ok and why == "holder-current"


def test_decide_stale_self_ok():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    ok, why = orch_lease.decide(_row("gzbai", now - timedelta(hours=2)), "gzbai", now)
    assert ok and why == "holder-stale-self"          # same host, just stale -> renew


def test_decide_fresh_other_holder_refused():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    ok, why = orch_lease.decide(_row("Sheikhs-Mini", now), "gzbai", now)
    assert ok is False and "refused" in why.lower()


def test_decide_expired_nonholder_FAILS_CLOSED():
    """THE FIX: an expired lease held by a DIFFERENT host must NOT auto-pass a
    non-holder (was the ~40h pen-gate no-op, bus #46618)."""
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    ok, why = orch_lease.decide(_row("Sheikhs-Mini", now - timedelta(hours=40)), "gzbai", now)
    assert ok is False                                # <-- was True before the fix
    assert "EXPIRED" in why and "take" in why


def test_missing_and_null_stay_failsafe():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    assert orch_lease.decide(None, "gzbai", now)[0] is True             # pre-migration
    assert orch_lease.decide(_row(None, now), "gzbai", now)[0] is True  # not self-stamped


def test_take_still_reclaims_expired_and_protects_fresh():
    """DR path preserved: `take` reclaims an EXPIRED lease, still protects a FRESH one."""
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    expired = _row("Sheikhs-Mini", now - timedelta(hours=40))
    got = orch_lease.apply_take(expired, "cc-orchestrator", "gzbai", now, reason="dr")
    assert got is not None and got["holder_host"] == "gzbai"           # reclaim works
    fresh = _row("Sheikhs-Mini", now)
    assert orch_lease.apply_take(fresh, "cc-orchestrator", "gzbai", now, reason="dr") is None
    assert orch_lease.apply_take(fresh, "cc-orchestrator", "gzbai", now, reason="dr",
                                 force=True) is not None                # --force still steals


# ── (4) _page: renew failure emits a P1 bus alert, best-effort ───────────────
def test_page_routes_p1_req_to_console():
    calls = {}
    def fake_run(argv, **kw):
        calls["argv"] = argv; calls["kw"] = kw
    orch_lease._page("subj", "body-text", _run=fake_run)
    a = calls["argv"]
    assert a[1].endswith("bus_send.py")
    assert "--to" in a and a[a.index("--to") + 1] == "orch-console"
    assert "--priority" in a and a[a.index("--priority") + 1] == "P1"
    assert "--req" in a
    assert a[a.index("--from") + 1] == "cc-orchestrator"
    assert calls["kw"].get("input") == "body-text"


def test_page_never_raises_on_run_error():
    def boom(*a, **k):
        raise RuntimeError("bus down")
    orch_lease._page("s", "b", _run=boom)   # must NOT raise
