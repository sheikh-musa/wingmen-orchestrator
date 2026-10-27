"""Console GO rulings #78373/#78383 went to 'cc-fleet-health-1' -- an instance id of a
singleton with no agent_status row -- and sat unread. Such an address is now REFUSED with
the base named. Live auditor-singleton instances (cc-finance-1 has a row) still pass."""
import pytest

from scripts import bus_send as bs


def guard(monkeypatch, to, row, singleton=True):
    monkeypatch.setattr(bs, "_agent_status_row", lambda a, dsn=None: row if a == to else None)
    monkeypatch.setattr(bs, "is_singleton_base", lambda b: singleton)
    bs.refuse_if_instance_stale_and_base_live(to)


def test_statusless_instance_of_singleton_is_refused_with_the_base_named(monkeypatch):
    with pytest.raises(SystemExit, match="Use --to cc-fleet-health instead"):
        guard(monkeypatch, "cc-fleet-health-1", None)


def test_live_instance_of_an_auditor_singleton_still_passes(monkeypatch):
    guard(monkeypatch, "cc-finance-1", ("cc-finance", True))   # live row -> no refusal


def test_statusless_instance_of_a_non_singleton_is_not_this_guards_concern(monkeypatch):
    guard(monkeypatch, "cc-scholar-7", None, singleton=False)


def test_non_instance_addresses_skip_the_check_entirely(monkeypatch):
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: pytest.fail("no DB call"))
    bs.refuse_if_instance_stale_and_base_live("cc-fleet-health")
