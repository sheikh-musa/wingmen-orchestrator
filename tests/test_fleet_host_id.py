"""Unit tests for fleet_host_id — the ONE stable host-identity source.

Resolution tiers (CAI-RESP-1436 + Nazim floor A-D):
  1. FLEET_HOST_ID env  — durable pin (boot-exported); silent when present.
  2. alias-match        — live hostname -> canonical via the git-tracked map;
                          a resilience net that MUST stay observable (not a
                          silent permanent substitute for the pin).
  3. raw fallback       — gethostname().split('.')[0]; LOUD warn (fragile).
A gethostname() throw PROPAGATES so consumers can fail-closed (lease) / surface
(matchers) explicitly (Nazim add D).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))

import fleet_host_id as fhi

MINI = "Sheikhs-Mini"


@pytest.fixture
def logs(monkeypatch):
    """Capture the observability sink."""
    recorded: list[str] = []
    monkeypatch.setattr(fhi, "_log", lambda m: recorded.append(m))
    return recorded


def _set_hostname(monkeypatch, name):
    monkeypatch.setattr(fhi.socket, "gethostname", lambda: name)


# ── tier 1: durable pin ──────────────────────────────────────────────────────

def test_env_pin_takes_precedence_and_never_touches_hostname(monkeypatch, logs):
    monkeypatch.setenv("FLEET_HOST_ID", MINI)
    # gethostname raising proves the pin path never calls it
    monkeypatch.setattr(fhi.socket, "gethostname",
                        lambda: (_ for _ in ()).throw(AssertionError("gethostname must not be called when pinned")))
    assert fhi.fleet_host_id() == MINI
    assert logs == []  # a pinned host is silent


# ── tier 2: alias-match (observable, not silent) ─────────────────────────────

def test_alias_match_resolves_a_known_flap(monkeypatch, logs):
    monkeypatch.delenv("FLEET_HOST_ID", raising=False)
    monkeypatch.setattr(fhi, "_load_map", lambda: {MINI: [MINI, "Sheikhs-Mac-mini", "Sheikhs-Mac-mini.local"]})
    _set_hostname(monkeypatch, "Sheikhs-Mac-mini.local")
    assert fhi.fleet_host_id() == MINI


def test_alias_match_is_observable_not_silent(monkeypatch, logs):
    # cai reminder: alias-match must not become a silent permanent state.
    monkeypatch.delenv("FLEET_HOST_ID", raising=False)
    monkeypatch.setattr(fhi, "_load_map", lambda: {MINI: [MINI, "Sheikhs-Mac-mini"]})
    _set_hostname(monkeypatch, "Sheikhs-Mac-mini")
    fhi.fleet_host_id()
    assert any("not pinned" in m.lower() for m in logs), f"alias-match must log it is unpinned: {logs}"


# ── tier 3: raw fallback (loud) ──────────────────────────────────────────────

def test_raw_fallback_returns_short_name_and_warns_loud(monkeypatch, logs):
    monkeypatch.delenv("FLEET_HOST_ID", raising=False)
    monkeypatch.setattr(fhi, "_load_map", lambda: {})  # nothing matches
    _set_hostname(monkeypatch, "some-random-host.local")
    assert fhi.fleet_host_id() == "some-random-host"
    assert any("fragile" in m.lower() and "warning" in m.lower() for m in logs), \
        f"unmapped host must LOUD-warn on the fragile fallback: {logs}"


# ── error behaviour (Nazim add D: the helper CAN throw) ──────────────────────

def test_gethostname_throw_propagates(monkeypatch, logs):
    # No pin, gethostname itself fails -> propagate so consumers decide
    # (lease fail-closed / matcher fail-toward-surfacing). Not swallowed.
    monkeypatch.delenv("FLEET_HOST_ID", raising=False)
    monkeypatch.setattr(fhi, "_load_map", lambda: {})
    monkeypatch.setattr(fhi.socket, "gethostname",
                        lambda: (_ for _ in ()).throw(OSError("no hostname")))
    with pytest.raises(OSError):
        fhi.fleet_host_id()


def test_corrupt_map_degrades_to_loud_fallback_not_crash(monkeypatch, logs):
    monkeypatch.delenv("FLEET_HOST_ID", raising=False)
    # _load_map must swallow read/parse errors -> {} so we degrade, not crash.
    monkeypatch.setattr(fhi, "MAP_PATH", Path("/nonexistent/fleet_hosts.json"))
    _set_hostname(monkeypatch, "Sheikhs-Mini")
    assert fhi.fleet_host_id() == "Sheikhs-Mini"  # falls through, no crash
    assert any("fragile" in m.lower() for m in logs)


# ── the shipped map: all three current hosts pinned (cai deploy condition) ────

def test_shipped_map_has_all_three_current_hosts():
    m = fhi._load_map()
    for canon in ("Sheikhs-Mini", "gzbai", "wingmen-core"):
        assert canon in m, f"fleet_hosts map missing current host {canon}: {list(m)}"


def test_shipped_map_covers_the_mini_flap_aliases():
    # The exact flap that caused the incident MUST resolve via the shipped map.
    m = fhi._load_map()
    assert fhi._match_alias("Sheikhs-Mac-mini.local", m) == "Sheikhs-Mini"
    assert fhi._match_alias("Sheikhs-Mac-mini", m) == "Sheikhs-Mini"
    assert fhi._match_alias("Sheikhs-Mini", m) == "Sheikhs-Mini"


# ── boot helper: resolve_pin (Nazim add A) ───────────────────────────────────

def test_resolve_pin_mapped_host_returns_canonical_and_mapped_true(monkeypatch):
    monkeypatch.setattr(fhi, "_load_map", lambda: {MINI: [MINI, "Sheikhs-Mac-mini.local"]})
    _set_hostname(monkeypatch, "Sheikhs-Mac-mini.local")
    label, mapped = fhi.resolve_pin()
    assert (label, mapped) == (MINI, True)


def test_resolve_pin_unmapped_host_flags_not_mapped(monkeypatch):
    monkeypatch.setattr(fhi, "_load_map", lambda: {})
    _set_hostname(monkeypatch, "brand-new-box.local")
    label, mapped = fhi.resolve_pin()
    assert (label, mapped) == ("brand-new-box", False)  # boot must WARN + not pin durably


# ── boot consistency check (Nazim add B, now testable) ───────────────────────

class _KnownHostConn:
    """Fake conn returning a scripted fetchone per execute (agent_status then lease UNION)."""
    def __init__(self, results):
        self._results = list(results)
        self._last = None
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params=None):
        self._last = self._results.pop(0) if self._results else None
    def fetchone(self):
        return self._last


def test_is_known_host_true_when_in_agent_status():
    assert fhi.is_known_host("Sheikhs-Mini", _KnownHostConn([(1,)])) is True


def test_is_known_host_true_when_only_a_lease_holder():
    # not in agent_status (None), but a lease holder_host (1,)
    assert fhi.is_known_host("gzbai", _KnownHostConn([None, (1,)])) is True


def test_is_known_host_false_when_unknown_everywhere():
    # a pin known to NO agent_status.host and NO lease -> boot B refuses (map misconfig).
    assert fhi.is_known_host("typo-host", _KnownHostConn([None, None])) is False
