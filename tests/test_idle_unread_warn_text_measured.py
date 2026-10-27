"""#78343: the idle-unread warn hardcoded '30min+'/'1800s+' (the SLOW tier) while the P1
fast tier fired at 5m -- the page asserted an age it never measured. It now states the
MEASURED oldest age + tier, or 'age not measured'. No live DB."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nervous_system"))

import wake_backstop_sweep as w  # noqa: E402


def capture(monkeypatch, summary):
    sent = []
    monkeypatch.setattr(w, "_unread_age_summary", lambda ids: summary)
    monkeypatch.setattr(w, "_bus_insert", lambda *a: sent.append(a) or 1)
    w._default_warn_fleet_health("cc-x-1", [78320])
    (_frm, _to, _t, _p, subject, body), = sent
    return subject, body


def test_warn_states_the_measured_age_and_tier(monkeypatch):
    s, b = capture(monkeypatch, "oldest 10m old, P1 fast tier (warns at 5m)")
    assert "oldest 10m old, P1 fast tier" in s and "oldest 10m old" in b


def test_warn_never_asserts_a_hardcoded_age(monkeypatch):
    s, b = capture(monkeypatch, "oldest 10m old, P1 fast tier (warns at 5m)")
    assert "30min+" not in s and "1800s+" not in b


def test_unmeasurable_age_says_so(monkeypatch):
    s, b = capture(monkeypatch, None)
    assert "age not measured" in s and "age not measured" in b


def test_summary_without_dsn_is_none(monkeypatch):
    monkeypatch.setattr(w, "_DSN", None)
    assert w._unread_age_summary([1]) is None
