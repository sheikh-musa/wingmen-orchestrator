"""Tests for singleton_liveness — process/tmux death detection for PROTECTED singletons.

TDD for the gap Nazim caught (35131/35135): cai was killed-not-relaunched and sat DEAD for
~30h because (a) no unread → wedge never fired, and (b) protected_agents were exempt from
death-detection. These pin the SAFETY-CRITICAL behavior: a recycle (tmux kept) is NOT dead,
a kill (no tmux) past a generous grace IS dead, and 'protected' does not mean 'unchecked'.
"""
from __future__ import annotations

import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nervous_system import singleton_liveness as sl  # noqa: E402


# ---- classify_dead(): the core alive/grace/dead decision -----------------------
def test_tmux_present_is_alive_regardless_of_hb():
    # A live body (tmux session exists) — even with an oldish hb reading (long inference,
    # boot lag) — is ALIVE. tmux presence is the ground truth.
    assert sl.classify_dead(tmux_present=True, hb_age_s=99999, threshold_s=1200) == "alive"

def test_no_tmux_but_fresh_hb_is_grace_not_dead():
    # No tmux yet but heartbeat is recent → mid-transition (booting / brief tmux blip).
    # Generous grace (Nazim #5): don't page a body that was alive moments ago.
    assert sl.classify_dead(tmux_present=False, hb_age_s=60, threshold_s=1200) == "grace"

def test_no_tmux_and_stale_hb_is_dead():
    # No tmux AND heartbeat stale past the threshold → genuinely DEAD (the cai-30h case).
    assert sl.classify_dead(tmux_present=False, hb_age_s=1800, threshold_s=1200) == "dead"

def test_dead_boundary_at_threshold():
    assert sl.classify_dead(tmux_present=False, hb_age_s=1200, threshold_s=1200) == "dead"
    assert sl.classify_dead(tmux_present=False, hb_age_s=1199, threshold_s=1200) == "grace"

def test_recycle_in_place_keeps_tmux_so_never_false_dead():
    # An in-place /clear recycle preserves the tmux session (verified on 2 Nazim recycles),
    # and its hb may briefly lag during boot — must read ALIVE, never dead.
    assert sl.classify_dead(tmux_present=True, hb_age_s=300, threshold_s=1200) == "alive"


# ---- checked-set: protected singletons, same-host, NOT self, NOT cross-host ----
def test_checked_agents_excludes_self_and_crosshost_hub():
    # Given the protected set, the monitor checks same-host non-self singletons only.
    # self (cc-fleet-health) can't check its own liveness if dead; the hub is cross-host (VPS).
    protected = ["cai", "cc-orchestrator", "nazim-console", "orch-console", "cc-fleet-health"]
    checked = sl.checked_agents(protected,
                                sessions={"cai": "cai", "orch-console": "nazim"},
                                self_agent="cc-fleet-health")
    assert "cai" in checked and "orch-console" in checked
    assert "cc-fleet-health" not in checked, "never death-check SELF"
    assert "cc-orchestrator" not in checked, "hub is cross-host — no session mapping, skip"

def test_checked_agents_maps_to_tmux_session():
    checked = sl.checked_agents(["cai"], sessions={"cai": "cai"}, self_agent="cc-fleet-health")
    assert checked == {"cai": "cai"}


# ---- page_recipient(): a DEAD body can't read its own page (Nazim follow-up #1) ------
def test_console_death_pages_the_hub_not_the_dead_console():
    # Paging a dead console = an unread nobody reads. Route to the hub (alive, can TG operator).
    assert sl.page_recipient("orch-console") == "cc-orchestrator"
    assert sl.page_recipient("nazim-console") == "cc-orchestrator"

def test_non_console_death_pages_the_console():
    # cai/other death → the console is alive to act on it.
    assert sl.page_recipient("cai") == "orch-console"


# ---- is_covered / backstop_action: the wedge-watchdog Gap-2 crux (Nazim 35141) ------
def test_is_covered_only_for_locally_mapped_singletons():
    # A singleton with a local tmux mapping is 'covered' by this monitor; the cross-host
    # hub (no mapping) is NOT — the wedge backstop must page for the uncovered set.
    assert sl.is_covered("cai", sessions={"cai": "cai", "orch-console": "nazim"}) is True
    assert sl.is_covered("cc-orchestrator", sessions={"cai": "cai"}) is False

def test_backstop_action_ok_nudge_does_nothing():
    assert sl.backstop_action("cai", nudge_ok=True, sessions={"cai": "cai"}) == "none"

def test_backstop_defers_for_a_covered_dead_singleton():
    # covered → the standalone monitor owns the page → DEFER (no double-fire).
    assert sl.backstop_action("cai", nudge_ok=False, sessions={"cai": "cai"}) == "defer"

def test_backstop_pages_for_an_uncovered_dead_singleton():
    # UNCOVERED (the cross-host hub) → the backstop is the ONLY detector → it MUST page.
    # This is the correctness crux: a dead hub must not be silently deferred to a monitor
    # that never checks it.
    assert sl.backstop_action("cc-orchestrator", nudge_ok=False, sessions={"cai": "cai"}) == "page"


# ── lease-freshness gate on the uncovered-hub DEAD-page (Nazim 37448, cross-host false-DEAD fix) ──
def test_backstop_lease_fresh_hub_is_alive_unreachable_not_dead():
    # THE fix: an uncovered singleton whose orch_lease is FRESH is ALIVE on its remote host — a
    # failed Mini->VPS nudge = unreachable-FROM-THE-MINI, NOT death. Must NOT DEAD-page.
    assert sl.backstop_action("cc-orchestrator", nudge_ok=False, sessions={"cai": "cai"},
                              lease_fresh=True) == "alive_unreachable"


def test_backstop_lease_stale_hub_still_pages_dead():
    # 35141 safety PRESERVED: a truly-dead hub stops renewing its lease -> stale -> still DEAD-page.
    assert sl.backstop_action("cc-orchestrator", nudge_ok=False, sessions={"cai": "cai"},
                              lease_fresh=False) == "page"


def test_backstop_lease_unknown_fails_safe_to_page():
    # lease_fresh unknown (None; DB unreadable / non-lease singleton) -> fail-safe: page (unchanged).
    assert sl.backstop_action("cc-orchestrator", nudge_ok=False, sessions={"cai": "cai"},
                              lease_fresh=None) == "page"


def test_backstop_lease_fresh_ignored_for_covered_singleton():
    # A COVERED singleton still DEFERS regardless of lease_fresh — the lease gate only applies to
    # the uncovered cross-host path.
    assert sl.backstop_action("cai", nudge_ok=False, sessions={"cai": "cai"},
                              lease_fresh=True) == "defer"


def test_backstop_lease_fresh_ignored_on_ok_nudge():
    assert sl.backstop_action("cc-orchestrator", nudge_ok=True, sessions={"cai": "cai"},
                              lease_fresh=True) == "none"


# ---- lease_fresh_from_row: the orch_lease freshness mapping (pure) ------------
def test_lease_fresh_from_row_fresh():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    assert sl.lease_fresh_from_row({"renewed_at": now - timedelta(seconds=60), "ttl_seconds": 900}, now) is True


def test_lease_fresh_from_row_expired():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    assert sl.lease_fresh_from_row({"renewed_at": now - timedelta(seconds=1000), "ttl_seconds": 900}, now) is False


def test_lease_fresh_from_row_indeterminate_is_none():
    from datetime import datetime, timezone
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    assert sl.lease_fresh_from_row(None, now) is None
    assert sl.lease_fresh_from_row({"ttl_seconds": 900}, now) is None          # no renewed_at
    assert sl.lease_fresh_from_row({"renewed_at": now}, now) is None           # no ttl


# ── retry-before-page on the pooler connect (port of ad38b99/f326d3e): a transient Supabase
#    pooler-DNS blip must NOT trip the singleton-liveness dead-man (PROBE FAILED); a PERSISTENT
#    failure still re-raises so main() pages loud. (2026-09-03 pooler-blip sweep, Nazim-approved.)

class _Transient(Exception):
    """Stand-in for psycopg2.OperationalError."""


def test_retry_recovers_after_transient_then_succeeds():
    n = {"c": 0}
    slept = []
    def op():
        n["c"] += 1
        if n["c"] < 3:
            raise _Transient("could not translate host name pooler.supabase.com")
        return "conn"
    assert sl._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=slept.append) == "conn"
    assert n["c"] == 3 and slept == [0.01, 0.02]


def test_retry_reraises_on_exhaustion():
    def op():
        raise _Transient("still down")
    with pytest.raises(_Transient, match="still down"):
        sl._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=lambda s: None)


def test_retry_does_not_retry_non_transient():
    n = {"c": 0}
    def op():
        n["c"] += 1
        raise ValueError("bad")
    with pytest.raises(ValueError):
        sl._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=lambda s: None)
    assert n["c"] == 1


def test_connect_recovers_from_transient_pooler_blip(monkeypatch):
    """_connect retries a transient OperationalError on the pooler connect and returns the
    connection — so sweep() proceeds and the dead-man does NOT false-fire."""
    import psycopg2
    monkeypatch.setattr(sl, "_dsn", lambda: "postgres://ignored")
    monkeypatch.setattr(sl, "_sleep", lambda s: None)
    n = {"c": 0}
    def fake_connect(dsn):
        n["c"] += 1
        if n["c"] == 1:
            raise psycopg2.OperationalError(
                "could not translate host name aws-1-ap-southeast-1.pooler.supabase.com")
        return "FAKE_CONN"
    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    assert sl._connect() == "FAKE_CONN"
    assert n["c"] == 2, "must retry the transient blip"


def test_connect_reraises_persistent_failure_dead_man_preserved(monkeypatch):
    """A GENUINE persistent outage must still surface (re-raise) so sweep()->main() pages
    PROBE FAILED — retries never swallow a real outage."""
    import psycopg2
    monkeypatch.setattr(sl, "_dsn", lambda: "postgres://ignored")
    monkeypatch.setattr(sl, "_sleep", lambda s: None)
    def fake_connect(dsn):
        raise psycopg2.OperationalError("persistent outage")
    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    with pytest.raises(psycopg2.OperationalError, match="persistent outage"):
        sl._connect()
