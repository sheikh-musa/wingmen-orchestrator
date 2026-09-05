"""hub_alive_evidence() + its use in commitment_sweeper (audit #1A, Nazim 37658).

The hub (cc-orchestrator) is cross-host; it renews orch_lease from its OWN host, not the
Mini heartbeat loop, so a FRESH lease is authoritative liveness even when its Mini-side
agent_status heartbeat is stale (the console wrote that row — audit #1). hub_alive_evidence()
is the single shared signal for that. Verified at source (Nazim confirmed scope (a)): of the
three named consumers only commitment_sweeper._live_agents needed it — singleton_liveness is
already lease-aware (agent_liveness(hub)='uncovered' + backstop_action gates on lease_fresh),
and fleet_health already spares the hub unconditionally via protected_agents.

Prod-clean: monkeypatch only, no DB / no load_dotenv (audit finding T1).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nervous_system import commitment_sweeper as cs  # noqa: E402
from nervous_system import singleton_liveness as sl  # noqa: E402


# ---- hub_alive_evidence(): fresh->alive, expired->dead, indeterminate->fail-safe alive ----

def test_fresh_lease_is_alive(monkeypatch):
    monkeypatch.setattr(sl, "hub_lease_fresh", lambda now=None: True)
    assert sl.hub_alive_evidence() is True


def test_expired_lease_is_dead(monkeypatch):
    # Only a POSITIVELY expired lease is dead — then the normal stale path proceeds.
    monkeypatch.setattr(sl, "hub_lease_fresh", lambda now=None: False)
    assert sl.hub_alive_evidence() is False


def test_indeterminate_lease_fails_safe_alive_and_logs(monkeypatch):
    # A read hiccup / missing row must NEVER drop a live hub from liveness. Fail-safe ALIVE,
    # and it must be LOGGED (dead-man's-switch: fail loud, never silently).
    monkeypatch.setattr(sl, "hub_lease_fresh", lambda now=None: None)
    logged = []
    monkeypatch.setattr(sl, "log", lambda m: logged.append(m))
    assert sl.hub_alive_evidence() is True
    assert logged, "indeterminate lease must be logged"


# ---- commitment_sweeper._live_agents: the hub is 'live' iff hub_alive_evidence() ----

class _Cur:
    """Minimal dict-row cursor stub — returns the seeded rows for any execute()."""
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows


def test_stale_hb_hub_counts_as_live_when_lease_fresh(monkeypatch):
    # The hb-window query does NOT return the hub (stale Mini hb). A fresh lease must still
    # make it 'live' so a commitment addressed to cc-orchestrator is not read as misaddressed.
    monkeypatch.setattr(sl, "hub_alive_evidence", lambda now=None: True)
    live = cs._live_agents(_Cur([{"agent_id": "cc-quality"}]))
    assert "cc-orchestrator" in live
    assert "cc-quality" in live  # the belt does not disturb the normal live set


def test_hub_excluded_when_lease_not_fresh(monkeypatch):
    # An expired-lease hub is genuinely stale — do NOT resurrect it into the live set.
    monkeypatch.setattr(sl, "hub_alive_evidence", lambda now=None: False)
    live = cs._live_agents(_Cur([{"agent_id": "cc-quality"}]))
    assert "cc-orchestrator" not in live
