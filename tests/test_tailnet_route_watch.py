"""Tests for scripts.tailnet_route_watch — pages when the Mini's 100.64/10 -> utun
route goes missing (console #79806: it broke silently ~08:36-10:05Z on 2026-10-28
and only surfaced as two misleading "peer inbound drop" symptoms)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts import tailnet_route_watch as t

GOOD = """   route to: 100.77.251.8
destination: 100.64.0.0
       mask: 255.192.0.0
  interface: utun5
      flags: <UP,DONE,CLONING,STATIC>
"""

# The 2026-10-28 broken shape: the tailnet IP falls through to the LAN default route.
BROKEN = """   route to: 100.77.251.8
destination: default
       mask: default
    gateway: 192.168.50.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
"""

NOW = datetime(2026, 10, 28, 10, 30, tzinfo=timezone.utc)


def test_parse_interface():
    assert t.parse_interface(GOOD) == "utun5"
    assert t.parse_interface(BROKEN) == "en0"


def test_parse_interface_missing_raises():
    with pytest.raises(t.MonitorError):
        t.parse_interface("route: writing to routing socket: not in table\n")


def test_all_peers_on_utun_is_healthy():
    res = t.evaluate({"gzbai": "utun5", "mac-studio": "utun5"})
    assert res is None


def test_any_peer_off_utun_is_a_finding():
    res = t.evaluate({"gzbai": "en0", "mac-studio": "utun5"})
    assert res["key"] == "tailnet-route-missing"
    assert res["bad"] == {"gzbai": "en0"}


def test_could_not_measure_is_its_own_finding():
    # A probe that fails reports 'could not measure', never 'healthy'.
    res = t.evaluate({"gzbai": None, "mac-studio": "utun5"})
    assert res["key"] == "tailnet-route-unmeasured"


def test_route_missing_outranks_unmeasured():
    res = t.evaluate({"gzbai": None, "mac-studio": "en0"})
    assert res["key"] == "tailnet-route-missing"


def test_plan_pages_once_per_cooldown_and_clears_on_recovery():
    f = t.evaluate({"gzbai": "en0"})
    page, state = t.plan(f, {}, NOW)
    assert page == "alert"
    state = t.mark(state, f["key"], NOW)
    page, state2 = t.plan(f, state, NOW + timedelta(minutes=10))
    assert page is None  # cooldown
    page, _ = t.plan(f, state, NOW + timedelta(minutes=t.COOLDOWN_MIN + 1))
    assert page == "alert"  # re-page after cooldown while still broken
    page, state3 = t.plan(None, state, NOW + timedelta(minutes=12))
    assert page == "recovered" and state3 == {}


def test_healthy_with_empty_state_sends_nothing():
    assert t.plan(None, {}, NOW) == (None, {})


def test_alert_body_names_the_fix_and_evidence():
    f = t.evaluate({"gzbai": "en0", "cubeasht": "en0"})
    subj, body = t.render(f)
    assert "100.64" in subj
    assert "en0" in body and "tailscale" in body.lower()


def test_run_pages_on_broken_and_dead_mans_on_send_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "probe", lambda ip: "en0")
    sent = []
    monkeypatch.setattr(t, "_send", lambda subj, body, prio, req: sent.append((subj, prio, req)) or 1)
    rc = t.run(tmp_path / "s.json", NOW, peers={"gzbai": "100.77.251.8"})
    assert rc == 1 and sent and sent[0][1] == "P1" and sent[0][2] is True

    def boom(*a, **k):
        raise RuntimeError("bus down")
    monkeypatch.setattr(t, "_send", boom)
    with pytest.raises(RuntimeError):  # fail LOUD: never swallow a failed page
        t.run(tmp_path / "s2.json", NOW, peers={"gzbai": "100.77.251.8"})
