"""Flap-simulation wet-prove (Nazim floor #4 / cai CAI-RESP-1436 FULL tier).

Encodes the invariant that a simulated hostname flap can NEITHER false-failover a
lease NOR false-gap a lane. Both symptoms of the 2026-09-20 incident (bus 41834)
derive from ONE value: the host identity that (a) the lease CAS compares against
holder_host and (b) the watchdog host-scoped matcher passes as its query param. If
that identity is STABLE across a flap, both symptoms are impossible.

An independent FULL-tier auditor also runs the live behavioural wet-prove (real
substrate). These unit assertions pin the deterministic core it verifies.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))

import fleet_host_id as fhi
import orch_lease as ol
import fleet_health_lease as fhl

MINI = "Sheikhs-Mini"
# The exact DHCP-derived names the Mini flapped between (bus 41834).
FLAP_NAMES = ["Sheikhs-Mini", "Sheikhs-Mini.local", "Sheikhs-Mac-mini", "Sheikhs-Mac-mini.local"]
MINI_ALIASES = {MINI: FLAP_NAMES}


def _flap(monkeypatch, name):
    monkeypatch.setattr(fhi.socket, "gethostname", lambda: name)


# ── the core invariant: identity is STABLE across the flap ───────────────────

@pytest.mark.parametrize("flapped", FLAP_NAMES)
def test_pinned_identity_is_stable_across_flap(monkeypatch, flapped):
    # Pinned (the durable prod state): NO flapped name can move the identity.
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    _flap(monkeypatch, flapped)
    assert ol._me() == MINI
    assert fhl._me() == MINI  # both leases + both matchers derive from these two.


@pytest.mark.parametrize("flapped", FLAP_NAMES)
def test_unpinned_identity_self_heals_via_alias_across_flap(monkeypatch, flapped):
    # Resilience net: even un-pinned, a KNOWN flap resolves to canonical via the map.
    monkeypatch.delenv("FLEET_HOST_ID", raising=False)
    monkeypatch.setattr(fhi, "_load_map", lambda: {c: sorted(set(a)) for c, a in MINI_ALIASES.items()})
    monkeypatch.setattr(fhi, "_log", lambda m: None)
    _flap(monkeypatch, flapped)
    assert ol._me() == MINI
    assert fhl._me() == MINI


def test_flap_never_desyncs_the_two_leases(monkeypatch):
    # The incident's latent bug: orch_lease stripped .local, fleet_health did not -> the
    # SRE lease got 'Sheikhs-Mac-mini.local' while the hub-scope used 'Sheikhs-Mac-mini'.
    # Unified now: the two identities agree under every flap variant.
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    for name in FLAP_NAMES:
        _flap(monkeypatch, name)
        assert ol._me() == fhl._me() == MINI


# ── no false-failover: the lease CAS predicate holds under flap ──────────────

def test_lease_renew_predicate_matches_under_flap_when_pinned(monkeypatch):
    # A lease held under holder_host=Sheikhs-Mini must still be renewable by THIS body
    # after a flap (the renew CAS is holder_host == _me()). Pinned -> _me() stays MINI ->
    # predicate holds -> renewal succeeds -> NO false-expiry/failover.
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    _flap(monkeypatch, "Sheikhs-Mac-mini.local")
    held_host = MINI  # what's stored in fleet_health_lease.holder_host
    assert fhl._me() == held_host, "renew CAS (holder_host == _me()) must still match post-flap"


# ── no false-gap: the matcher scopes its query to the STABLE id under flap ────

class _SinkConn:
    def __init__(self, sink):
        self._sink = sink
    def cursor(self):
        return _SinkCursor(self._sink)


class _SinkCursor:
    def __init__(self, sink):
        self._sink = sink
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql="", params=None, *a, **k):
        self._sink["params"] = params
    def fetchall(self):
        return []


def test_matcher_scopes_to_stable_id_not_the_flapped_name(monkeypatch):
    # The host-scoped map query must be parameterised with the STABLE identity, so a
    # row written under host='Sheikhs-Mini' is NOT excluded when gethostname flaps.
    import nervous_system.lane_wedge_watchdog as w
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    _flap(monkeypatch, "Sheikhs-Mac-mini.local")
    sink = {}
    w.agent_status_lane_map(_SinkConn(sink))
    assert sink["params"] == (MINI,), \
        f"matcher must host-scope to the stable id, not the flapped name: {sink['params']}"


# ── no regression: pin-unset + unmapped degrades to TODAY's behavior ─────────

def test_unpinned_unmapped_degrades_to_todays_flappy_behavior(monkeypatch):
    monkeypatch.delenv("FLEET_HOST_ID", raising=False)
    monkeypatch.setattr(fhi, "_load_map", lambda: {})  # nothing matches
    monkeypatch.setattr(fhi, "_log", lambda m: None)
    _flap(monkeypatch, "Sheikhs-Mac-mini.local")
    # exactly the old orch_lease._me() behavior: gethostname().split('.')[0]
    assert ol._me() == "Sheikhs-Mac-mini"


# ── Nazim add D: per-consumer error behaviour when the resolver THROWS ────────

def _raise(*a, **k):
    raise OSError("no stable identity resolvable")


def test_matcher_fails_toward_surfacing_when_identity_unresolved(monkeypatch):
    import nervous_system.lane_wedge_watchdog as w
    monkeypatch.setattr(w, "log", lambda m: None)
    monkeypatch.setattr(w.orch_lease, "_me", _raise)
    # No host map / no fallback rescue -> sessions SURFACE (a false gap is safe), not a crash.
    assert w.agent_status_lane_map(object()) == ({}, set())
    assert w.agent_status_base_fallback(object()) == {}


def test_fleet_health_lease_take_and_renew_FAIL_CLOSED_when_identity_unresolved(monkeypatch):
    monkeypatch.setattr(fhl, "_me", _raise)
    # If the identity can't be resolved, the lease must NEVER touch the DB.
    monkeypatch.setattr(fhl.psycopg, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect when identity is unknown")))
    assert fhl.cmd_take(None, "reason") == 3
    assert fhl.cmd_renew(None) == 3


def test_orch_lease_take_and_renew_FAIL_CLOSED_when_identity_unresolved(monkeypatch):
    monkeypatch.setattr(ol, "_me", _raise)
    monkeypatch.setattr(ol.psycopg, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect when identity is unknown")))
    assert ol.cmd_take("reason") == 3
    assert ol.cmd_renew() == 3


# ── PR #129 audit: WRITER/READER end-to-end (a real drift-named row through the matcher) ──
# The prior suite only asserted the query PARAM via a sink cursor. This drives an actual
# agent_status row through the shipped host filter (host = <reader _me()> OR host IS NULL),
# proving a row WRITTEN with the resolved id is MATCHED by the pinned reader under a flap —
# and that a row written with the RAW flapped hostname (the pre-fix writer) is excluded.

class _FilterConn:
    """Fake conn that APPLIES the matcher's host filter: fetchall returns only rows whose
    host == the executed host param OR host IS NULL. Rows are (col1..colN, host)."""
    def __init__(self, rows, ncols):
        self._rows, self._ncols, self._host = rows, ncols, object()
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params=None):
        self._host = params[0] if params else None
    def fetchall(self):
        return [r[:self._ncols] for r in self._rows if r[-1] == self._host or r[-1] is None]


def test_writer_resolved_row_maps_through_pinned_matcher_under_flap(monkeypatch):
    import nervous_system.lane_wedge_watchdog as w
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    _flap(monkeypatch, "Sheikhs-Mac-mini.local")
    # Row as the FIXED writer stamps it: host=<resolved>=Sheikhs-Mini.
    conn = _FilterConn([("exams", "cc-cosem-exams", "cc-cosem-exams-1", MINI)], ncols=3)
    mapped, _ = w.agent_status_lane_map(conn)
    assert mapped.get("exams") == "cc-cosem-exams", "a writer-resolved row must map under a flap"


def test_raw_flapped_writer_row_is_excluded_documenting_the_desync(monkeypatch):
    import nervous_system.lane_wedge_watchdog as w
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    _flap(monkeypatch, "Sheikhs-Mac-mini.local")
    # Row as the OLD raw writer stamped it: host='Sheikhs-Mac-mini' != pinned reader -> excluded.
    conn = _FilterConn([("exams", "cc-cosem-exams", "cc-cosem-exams-1", "Sheikhs-Mac-mini")], ncols=3)
    mapped, _ = w.agent_status_lane_map(conn)
    assert "exams" not in mapped, "the desync is real — this is WHY the writers were unified"


def test_base_fallback_host_scopes_to_stable_id_under_flap(monkeypatch):
    import nervous_system.lane_wedge_watchdog as w
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    _flap(monkeypatch, "Sheikhs-Mac-mini.local")
    conn = _FilterConn([("exams", "cc-cosem-exams", MINI)], ncols=2)  # (tmux_session, ident, host)
    fb = w.agent_status_base_fallback(conn)
    assert fb.get("exams") == "cc-cosem-exams"


def test_writer_and_reader_resolve_the_same_identity_under_flap(monkeypatch):
    # The invariant behind the end-to-end: the WRITER's self-resolution (fleet_host_id) and
    # the READERS' (_me()) produce ONE value under a flap, so writes are always matched.
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    for name in FLAP_NAMES:
        _flap(monkeypatch, name)
        writer = fhi.fleet_host_id()          # gzb_hub_heartbeat / launcher `current`
        assert writer == ol._me() == fhl._me() == MINI
