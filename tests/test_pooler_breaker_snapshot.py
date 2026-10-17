"""pooler_breaker_snapshot.py — dead-man rework (held_commitment #315).

The alert page must survive the DB bus being unreachable (the same pooler that just
tripped is what the page's own INSERT goes over). Covers: save-before-page never
blocks delivery, a failed DB insert spools + falls back to the bot-API path instead
of raising, and a later run replays the spool and purges whatever lands.
Everything here is stubbed: no real DB connection, no real Telegram send, no live
state/spool files.
"""
import importlib.util
import json
import pathlib

import pytest

_MOD = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "pooler_breaker_snapshot.py"
_spec = importlib.util.spec_from_file_location("pooler_breaker_snapshot", _MOD)
pbs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pbs)


@pytest.fixture(autouse=True)
def _tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(pbs, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(pbs, "SPOOL", tmp_path / "spool.json")
    monkeypatch.setattr(pbs, "ALERT_LOG_DIR", tmp_path / "alerts")


def test_page_success_writes_no_spool_entry(monkeypatch):
    inserted = []
    monkeypatch.setattr(pbs, "_db_insert", lambda subject, body: inserted.append((subject, body)))
    bot_calls = []
    monkeypatch.setattr(pbs, "_bot_fallback", lambda subject, body: bot_calls.append((subject, body)))

    pbs._page("subject-a", "body-a")

    assert inserted == [("subject-a", "body-a")]
    assert bot_calls == []
    assert pbs._load_spool() == []
    saved = list(pbs.ALERT_LOG_DIR.glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())["subject"] == "subject-a"


def test_page_db_failure_spools_and_falls_back_never_raises(monkeypatch):
    def _boom(subject, body):
        raise RuntimeError("simulated DB outage (pooler tripped)")
    monkeypatch.setattr(pbs, "_db_insert", _boom)
    bot_calls = []
    monkeypatch.setattr(pbs, "_bot_fallback", lambda subject, body: bot_calls.append((subject, body)))

    pbs._page("subject-b", "body-b")  # must NOT raise

    assert bot_calls == [("subject-b", "body-b")]
    spool = pbs._load_spool()
    assert len(spool) == 1
    assert spool[0]["subject"] == "subject-b"
    assert spool[0]["body"] == "body-b"
    # save-before-page still happened despite the DB failure
    saved = list(pbs.ALERT_LOG_DIR.glob("*.json"))
    assert len(saved) == 1


def test_flush_spool_replays_and_purges_delivered_entries(monkeypatch):
    pbs._save_spool([
        {"subject": "old-1", "body": "b1", "saved_at": "t1"},
        {"subject": "old-2", "body": "b2", "saved_at": "t2"},
    ])
    delivered = []

    def _insert(subject, body):
        if subject == "old-1":
            delivered.append(subject)
            return
        raise RuntimeError("still down")
    monkeypatch.setattr(pbs, "_db_insert", _insert)

    pbs._flush_spool()

    assert delivered == ["old-1"]
    remaining = pbs._load_spool()
    assert [e["subject"] for e in remaining] == ["old-2"]


def test_flush_spool_noop_on_empty_spool(monkeypatch):
    calls = []
    monkeypatch.setattr(pbs, "_db_insert", lambda subject, body: calls.append(subject))
    pbs._flush_spool()
    assert calls == []


def test_main_fresh_trip_with_db_down_does_not_crash_and_saves_state(monkeypatch):
    monkeypatch.setattr(pbs, "_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(pbs, "_breaker_state", lambda dsn: "tripped")
    monkeypatch.setattr(pbs, "_snapshot", lambda: ["mini pid=1 cmd=python"])
    monkeypatch.setattr(pbs, "_recent_jobs", lambda: [])

    def _boom(subject, body):
        raise RuntimeError("simulated DB outage")
    monkeypatch.setattr(pbs, "_db_insert", _boom)
    bot_calls = []
    monkeypatch.setattr(pbs, "_bot_fallback", lambda subject, body: bot_calls.append(subject))

    rc = pbs.main()  # must return cleanly, not raise

    assert rc == 0
    assert bot_calls  # the fallback fired
    assert json.loads(pbs.STATE.read_text())["state"] == "tripped"
    assert len(pbs._load_spool()) == 1


def test_main_skips_flush_while_still_tripped(monkeypatch):
    pbs._save_spool([{"subject": "stuck", "body": "b", "saved_at": "t"}])
    monkeypatch.setattr(pbs, "_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(pbs, "_breaker_state", lambda dsn: "tripped")
    monkeypatch.setattr(pbs, "_snapshot", lambda: [])
    monkeypatch.setattr(pbs, "_recent_jobs", lambda: [])
    monkeypatch.setattr(pbs, "_bot_fallback", lambda subject, body: None)

    flush_calls = []
    orig_flush = pbs._flush_spool
    def _spy():
        flush_calls.append(1)
        return orig_flush()
    monkeypatch.setattr(pbs, "_flush_spool", _spy)
    # the fresh-trip page itself will try to insert and fail -> spools a 2nd entry
    monkeypatch.setattr(pbs, "_db_insert", lambda subject, body: (_ for _ in ()).throw(RuntimeError("down")))

    pbs.main()

    assert flush_calls == []  # never attempted while tripped
    assert len(pbs._load_spool()) == 2  # the pre-existing entry, untouched + the new page


def test_main_clear_state_flushes_spool(monkeypatch):
    pbs._save_spool([{"subject": "stuck", "body": "b", "saved_at": "t"}])
    monkeypatch.setattr(pbs, "_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(pbs, "_breaker_state", lambda dsn: "clear")
    delivered = []
    monkeypatch.setattr(pbs, "_db_insert", lambda subject, body: delivered.append(subject))

    pbs.main()

    assert delivered == ["stuck"]
    assert pbs._load_spool() == []
