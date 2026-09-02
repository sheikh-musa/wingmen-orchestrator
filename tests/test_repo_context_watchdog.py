"""Pure-unit tests for scripts/repo_context_watchdog.

No DB / no network. Exercises the freshness-check logic + timestamp parsing +
active-repo filter + degrade-text composition. The DB/sweep path (run) is I/O and
covered by the durable-guarantee logic these pure functions implement.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.repo_context_watchdog import (
    RepoFreshness,
    active_repo_names,
    evaluate_freshness,
    _parse_ts,
    _degrade_text,
    _retry,
)

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
TWO_H = 2 * 3600


def test_module_imports():
    # Smoke: the watchdog module imports cleanly with no PYTHONPATH reliance.
    import scripts.repo_context_watchdog as m
    assert hasattr(m, "evaluate_freshness")
    assert hasattr(m, "run")
    assert m._FRESH_THRESHOLD_S > 0


# --- active_repo_names ------------------------------------------------------ #

def test_active_repo_names_filters_status_active():
    repos = [
        {"name": "ihsanos", "status": "active"},
        {"name": "dookana", "status": "frozen-maintenance"},
        {"name": "dawah-pipeline", "status": "specced"},
        {"name": "cosem-tdu", "status": "active"},
    ]
    assert active_repo_names(repos) == ["ihsanos", "cosem-tdu"]


def test_active_repo_names_skips_nameless():
    repos = [{"status": "active"}, {"name": "", "status": "active"}, {"name": "x", "status": "active"}]
    assert active_repo_names(repos) == ["x"]


# --- evaluate_freshness ----------------------------------------------------- #

def test_fresh_repo_not_stale():
    fresh_ts = NOW - timedelta(minutes=10)
    rows = evaluate_freshness({"ihsanos": fresh_ts}, ["ihsanos"], NOW, TWO_H)
    assert len(rows) == 1
    assert rows[0].repo == "ihsanos"
    assert rows[0].stale is False
    assert rows[0].age_s == 600
    assert rows[0].reason == ""


def test_stale_repo_flagged_over_threshold():
    old_ts = NOW - timedelta(hours=13)  # the actual 13-day-class freeze, scaled down
    rows = evaluate_freshness({"ihsanos": old_ts}, ["ihsanos"], NOW, TWO_H)
    assert rows[0].stale is True
    assert rows[0].reason == "stale (age > threshold)"
    assert rows[0].age_s == 13 * 3600


def test_boundary_exactly_at_threshold_is_fresh():
    # age == threshold is NOT stale (strictly greater-than trips it).
    ts = NOW - timedelta(seconds=TWO_H)
    rows = evaluate_freshness({"r": ts}, ["r"], NOW, TWO_H)
    assert rows[0].stale is False
    # one second older trips it
    ts2 = NOW - timedelta(seconds=TWO_H + 1)
    rows2 = evaluate_freshness({"r": ts2}, ["r"], NOW, TWO_H)
    assert rows2[0].stale is True


def test_missing_row_is_stale_with_no_age():
    # active repo with no repo_context row at all -> stale, age None.
    rows = evaluate_freshness({}, ["newrepo"], NOW, TWO_H)
    assert rows[0].stale is True
    assert rows[0].age_s is None
    assert rows[0].reason == "no repo_context row"


def test_explicit_none_value_is_stale():
    rows = evaluate_freshness({"r": None}, ["r"], NOW, TWO_H)
    assert rows[0].stale is True
    assert rows[0].age_s is None


def test_naive_datetime_treated_as_utc():
    naive = (NOW - timedelta(minutes=5)).replace(tzinfo=None)
    rows = evaluate_freshness({"r": naive}, ["r"], NOW, TWO_H)
    assert rows[0].stale is False
    assert rows[0].age_s == 300


def test_sort_stale_first_then_oldest():
    data = {
        "fresh": NOW - timedelta(minutes=1),
        "stale_old": NOW - timedelta(hours=20),
        "stale_less": NOW - timedelta(hours=3),
        "missing": None,
    }
    active = ["fresh", "stale_less", "stale_old", "missing"]
    rows = evaluate_freshness(data, active, NOW, TWO_H)
    order = [r.repo for r in rows]
    # stale first; among stale, missing (sentinel age) first, then oldest age.
    assert order[0] == "missing"
    assert order[1] == "stale_old"
    assert order[2] == "stale_less"
    assert order[3] == "fresh"
    assert rows[-1].stale is False


def test_only_active_repos_evaluated():
    # a repo present in the DB but not active is ignored; only active ones judged.
    data = {"ihsanos": NOW - timedelta(minutes=1), "dookana": NOW - timedelta(days=30)}
    rows = evaluate_freshness(data, ["ihsanos"], NOW, TWO_H)
    assert [r.repo for r in rows] == ["ihsanos"]
    assert rows[0].stale is False


# --- _parse_ts -------------------------------------------------------------- #

def test_parse_ts_variants():
    assert _parse_ts(None) is None
    assert _parse_ts("") is None
    assert _parse_ts("not-a-date") is None
    z = _parse_ts("2026-07-22T12:00:00Z")
    assert z == datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
    off = _parse_ts("2026-07-22T16:00:00+04:00")
    assert off.utcoffset() == timedelta(hours=4)
    micro = _parse_ts("2026-07-22T12:00:00.123456+00:00")
    assert micro.tzinfo is not None
    # a naive datetime object comes back tz-aware UTC
    dt = _parse_ts(datetime(2026, 7, 22, 12, 0, 0))
    assert dt.tzinfo == timezone.utc


# --- _degrade_text ---------------------------------------------------------- #

def test_degrade_text_names_stale_repos():
    stale = [
        RepoFreshness("ihsanos", None, None, True, "no repo_context row"),
        RepoFreshness("cosem-tdu", "2026-07-09T12:00:00+00:00", 13 * 24 * 3600, True, "stale (age > threshold)"),
    ]
    text = _degrade_text(stale, TWO_H)
    assert "ihsanos" in text
    assert "cosem-tdu" in text
    # the page is actionable / mentions repo_context so the operator knows what froze
    assert "repo_context" in text


# --- _retry (transient-blip suppression, dead-man preserved) ---------------- #
#
# Overnight incident: a single transient DNS failure resolving the Supabase
# pooler host propagated to the __main__ dead-man page and woke the operator.
# _retry wraps the DB connect so a transient blip that recovers is suppressed,
# while a GENUINE persistent failure STILL re-raises (dead-man page fires).

class _Transient(Exception):
    """Stand-in for psycopg.OperationalError (DNS/connect blip)."""


def test_retry_recovers_after_transient_failures_without_raising():
    # Fails twice (transient), succeeds on the 3rd try -> returns, no exception
    # propagates -> the dead-man page never fires for a blip that self-heals.
    attempts = {"n": 0}
    slept: list[float] = []

    def op():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _Transient("failed to resolve host pooler.supabase.com")
        return "rows"

    result = _retry(op, attempts=3, base_delay_s=0.01,
                    retry_on=_Transient, sleep=slept.append)

    assert result == "rows"
    assert attempts["n"] == 3
    assert slept == [0.01, 0.02]  # linear backoff between the 2 failed tries


def test_retry_reraises_after_exhausting_attempts():
    # A GENUINE, persistent failure must still surface after the retry budget —
    # the dead-man page fires on real death, retries only buy a short grace window.
    attempts = {"n": 0}
    slept: list[float] = []

    def op():
        attempts["n"] += 1
        raise _Transient("host still unresolvable")

    with pytest.raises(_Transient, match="host still unresolvable"):
        _retry(op, attempts=3, base_delay_s=0.01,
               retry_on=_Transient, sleep=slept.append)

    assert attempts["n"] == 3          # exactly the budget, no more
    assert slept == [0.01, 0.02]       # slept between tries, not after the last


def test_retry_does_not_retry_non_transient_errors():
    # An error OUTSIDE the retry class (e.g. a programming/SQL bug) must surface
    # immediately — retrying it only delays a page that should fire fast.
    attempts = {"n": 0}
    slept: list[float] = []

    def op():
        attempts["n"] += 1
        raise ValueError("bad query")

    with pytest.raises(ValueError, match="bad query"):
        _retry(op, attempts=3, base_delay_s=0.01,
               retry_on=_Transient, sleep=slept.append)

    assert attempts["n"] == 1          # tried once, did not retry
    assert slept == []


def test_retry_succeeds_first_try_no_sleep():
    slept: list[float] = []
    result = _retry(lambda: 42, attempts=3, base_delay_s=0.01,
                    retry_on=_Transient, sleep=slept.append)
    assert result == 42
    assert slept == []


def test_fetch_updated_at_retries_transient_connect_blip(monkeypatch):
    # Integration proof mirroring the real incident: psycopg.connect raises
    # OperationalError once (DNS blip), then succeeds. fetch_updated_at must
    # recover and return rows rather than letting it reach the dead-man page.
    import asyncio
    import psycopg
    import scripts.repo_context_watchdog as wd

    calls = {"n": 0}

    class _FakeCursor:
        def execute(self, *a, **k):
            pass
        def fetchall(self):
            return [("ihsanos", NOW)]

    class _FakeConn:
        def cursor(self):
            return _CtxCursor()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class _CtxCursor:
        def __enter__(self):
            return _FakeCursor()
        def __exit__(self, *a):
            return False

    def fake_connect(dsn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.OperationalError(
                "connection failed: failed to resolve host "
                "aws-1-ap-southeast-2.pooler.supabase.com"
            )
        return _FakeConn()

    monkeypatch.setenv("DATABASE_URL", "postgres://ignored")
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(wd, "_sleep", lambda s: None)  # no real backoff delay

    out = asyncio.run(wd.fetch_updated_at())

    assert calls["n"] == 2                       # retried the transient blip
    assert out == {"ihsanos": NOW}               # recovered, returned real rows
