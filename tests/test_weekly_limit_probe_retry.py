"""Probe hardening: a single transient read-timeout must RETRY, not blind the watch.

Context (Nazim #27655, 2026-08-18): the Musa usage probe timed out once and the
weekly watch went dark on Musa — the exact pool the hub sits on at ~98%. A monitor
that goes silent on ONE slow read is itself a 'gauge reads green' failure
(absence-of-signal treated as OK). probe_pool now retries transient network/timeout
errors before raising (which pages); HTTPError (429/over-limit) is NOT retried — it
carries the rate-limit headers and is used directly.
"""
import urllib.error
from unittest.mock import MagicMock

import pytest

from nervous_system import weekly_limit_monitor as wl


def _fake_resp(u7d=0.5):
    r = MagicMock()
    r.headers = {"anthropic-ratelimit-unified-7d-utilization": str(u7d)}
    r.status = 200
    return r


@pytest.fixture(autouse=True)
def _fast_and_tokened(monkeypatch):
    monkeypatch.setattr(wl, "PROBE_BACKOFF", 0, raising=False)
    monkeypatch.setattr(wl, "PROBE_RETRIES", 3, raising=False)
    monkeypatch.setattr(wl, "_load_token", lambda kind, ref: "dummy-token")


def test_transient_timeout_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(req, timeout=30):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("timed out")
        return _fake_resp(0.5)

    monkeypatch.setattr(wl.urllib.request, "urlopen", flaky)
    p = wl.probe_pool("Musa")
    assert calls["n"] == 3, "should have retried until the 3rd attempt succeeded"
    assert abs(p["u7d"] - 0.5) < 1e-9


def test_raises_only_after_all_retries_exhausted(monkeypatch):
    calls = {"n": 0}

    def always_timeout(req, timeout=30):
        calls["n"] += 1
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(wl.urllib.request, "urlopen", always_timeout)
    with pytest.raises(Exception):
        wl.probe_pool("Musa")
    assert calls["n"] == 3, "should have attempted exactly PROBE_RETRIES times before raising"


def test_httperror_is_not_retried_and_headers_used(monkeypatch):
    # A 429/over-limit carries the rate-limit headers — use them, never retry/blind.
    calls = {"n": 0}

    def http429(req, timeout=30):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url="x", code=429, msg="rate limited",
            hdrs={"anthropic-ratelimit-unified-7d-utilization": "1.0"}, fp=None)

    monkeypatch.setattr(wl.urllib.request, "urlopen", http429)
    p = wl.probe_pool("Musa")
    assert calls["n"] == 1, "HTTPError must NOT be retried"
    assert p["u7d"] == 1.0 and p["http"] == 429


def test_http_529_overload_is_retried_then_succeeds(monkeypatch):
    # A 529 (Anthropic API OVERLOAD) is transient and carries NO rate-limit body —
    # it must be retried like a network blip, not mistaken for an auth/shape break
    # (Nazim #37094; the musa2 06:00Z false PROBE-FAILED page).
    calls = {"n": 0}

    def overloaded_then_ok(req, timeout=30):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(url="x", code=529, msg="overloaded", hdrs={}, fp=None)
        return _fake_resp(0.42)

    monkeypatch.setattr(wl.urllib.request, "urlopen", overloaded_then_ok)
    p = wl.probe_pool("musa2")
    assert calls["n"] == 3, "a 529 must be RETRIED, not immediately treated as a break"
    assert abs(p["u7d"] - 0.42) < 1e-9


def test_persistent_529_raises_after_retries_not_immediately(monkeypatch):
    # A 529 that PERSISTS through all retries still raises (usage genuinely unwatched
    # -> a real page is warranted), but only AFTER exhausting retries — never on the
    # first blip (that was the false-alarm bug).
    calls = {"n": 0}

    def always_529(req, timeout=30):
        calls["n"] += 1
        raise urllib.error.HTTPError(url="x", code=529, msg="overloaded", hdrs={}, fp=None)

    monkeypatch.setattr(wl.urllib.request, "urlopen", always_529)
    with pytest.raises(Exception):
        wl.probe_pool("musa2")
    assert calls["n"] == 3, "a 529 must be retried PROBE_RETRIES times before raising"


def test_http_401_auth_break_is_not_retried_and_raises(monkeypatch):
    # A real auth break (401, no rate-limit header) is NOT transient — it must NOT be
    # retried (that only delays a real page); it raises promptly. Guards the retryable
    # set from being over-broad.
    calls = {"n": 0}

    def http401(req, timeout=30):
        calls["n"] += 1
        raise urllib.error.HTTPError(url="x", code=401, msg="unauthorized", hdrs={}, fp=None)

    monkeypatch.setattr(wl.urllib.request, "urlopen", http401)
    with pytest.raises(Exception):
        wl.probe_pool("Musa")
    assert calls["n"] == 1, "a 401 auth break must NOT be retried"
