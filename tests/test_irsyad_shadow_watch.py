"""Tests for scripts/irsyad_shadow_watch.py — retry-before-crash-page on the pooler connect.

2026-09-03 pooler-blip sweep (Nazim-approved, his gate — irsyad domain): a transient Supabase
pooler-DNS blip on the loop connect fired a spurious P1 "CRASHED" + an unnecessary launchd
restart. The `_retry` port absorbs a transient blip; a PERSISTENT failure still raises so the
CRASHED notice + launchd restart (real crash recovery) fire as intended.
"""
from __future__ import annotations

import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from scripts import irsyad_shadow_watch as s


class _Transient(Exception):
    """Stand-in for psycopg.OperationalError."""


def test_retry_recovers_after_transient():
    n = {"c": 0}
    slept = []
    def op():
        n["c"] += 1
        if n["c"] < 3:
            raise _Transient("could not translate host name pooler.supabase.com")
        return "ok"
    assert s._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=slept.append) == "ok"
    assert n["c"] == 3 and slept == [0.01, 0.02]


def test_retry_reraises_on_exhaustion():
    def op():
        raise _Transient("down")
    with pytest.raises(_Transient):
        s._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=lambda x: None)


def test_retry_skips_non_transient():
    n = {"c": 0}
    def op():
        n["c"] += 1
        raise ValueError("x")
    with pytest.raises(ValueError):
        s._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=lambda x: None)
    assert n["c"] == 1


def test_connect_recovers_from_transient_blip(monkeypatch):
    """_connect retries a transient OperationalError and returns the connection — so the loop
    proceeds and NO spurious CRASHED page / launchd restart fires."""
    import psycopg
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://ignored")
    monkeypatch.setattr(s, "_sleep", lambda x: None)
    n = {"c": 0}
    def fake_connect(dsn, **k):
        n["c"] += 1
        if n["c"] == 1:
            raise psycopg.OperationalError(
                "could not translate host name aws-1-ap-southeast-1.pooler.supabase.com")
        return "FAKE_CONN"
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    assert s._connect() == "FAKE_CONN"
    assert n["c"] == 2, "must retry the transient blip"


def test_connect_reraises_persistent_failure(monkeypatch):
    """A GENUINE persistent failure must still raise so the loop's except fires the CRASHED
    notice + launchd restart (real crash recovery preserved)."""
    import psycopg
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://ignored")
    monkeypatch.setattr(s, "_sleep", lambda x: None)
    def fake_connect(dsn, **k):
        raise psycopg.OperationalError("persistent outage")
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    with pytest.raises(psycopg.OperationalError, match="persistent outage"):
        s._connect()
