import subprocess

from legacy.reel_triage import worker
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def _insert(reel_db, code):
    reel_db.cursor().execute(
        "insert into reel_inbox (shortcode, url, source) values (%s, %s, 'share_link')",
        (code, f"https://instagram.com/reel/{code}"))


def test_process_one_marks_triaged(reel_db, monkeypatch):
    _insert(reel_db, "OK1")
    monkeypatch.setattr(worker.fetcher, "fetch", lambda url, d: "/tmp/x.mp4")
    monkeypatch.setattr(worker.fetcher, "keyframes", lambda m, d: [])
    monkeypatch.setattr(worker.fetcher, "cleanup_media", lambda m: None)
    monkeypatch.setattr(worker, "transcribe", lambda m: "hello transcript")
    monkeypatch.setattr(worker, "structure", lambda t, f: {
        "topic": "t", "claim": "c", "evidence_grade": "cited", "action": "a",
        "effort": "5min", "impact": 5, "confidence": 1.0, "priority": 5.0})
    assert worker.process_one(reel_db) is True
    row = reel_db.cursor().execute(
        "select status, transcript, priority from reel_inbox where shortcode='OK1'"
    ).fetchone()
    assert row["status"] == "triaged"
    assert row["transcript"] == "hello transcript"
    assert row["priority"] == 5.0


def test_process_one_writes_full_stderr_on_fetch_failure(reel_db, monkeypatch):
    _insert(reel_db, "FAIL1")

    def boom(url, d):
        raise subprocess.CalledProcessError(1, "yt-dlp", stderr="LONG STDERR ERROR DETAIL")

    monkeypatch.setattr(worker.fetcher, "fetch", boom)
    assert worker.process_one(reel_db) is True
    row = reel_db.cursor().execute(
        "select status, error from reel_inbox where shortcode='FAIL1'").fetchone()
    assert "LONG STDERR ERROR DETAIL" in row["error"]
    assert row["status"] == "inbox"  # stays inbox; surfaced as needs-manual


def test_process_one_returns_false_when_empty(reel_db):
    assert worker.process_one(reel_db) is False


def test_claim_is_oldest_first_and_skips_errored(reel_db, monkeypatch):
    _insert(reel_db, "OLD")
    _insert(reel_db, "NEW")
    # mark NEW as errored so it is not claimable
    reel_db.cursor().execute("update reel_inbox set error='x' where shortcode='NEW'")
    claimed = worker._claim(reel_db)
    assert claimed["shortcode"] == "OLD"
