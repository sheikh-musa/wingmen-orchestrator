"""Tests for nervous_system.bug_report_channel_poll (Musa op#27000/#27004).

Covers: NRIC masking, compose truncation, idempotency / no-double-post,
dry-run posts nothing, missing-bug skip, and fail-loud-without-stamp on an
enqueue failure. DB + Telegram send are stubbed (no network, no real DB).
"""
from __future__ import annotations

import pytest

from nervous_system import bug_report_channel_poll as brc


# ── Fake DB ────────────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM coord_dispatch_queue q" in s:
            # unposted bug-report rows = queue rows minus those in the marker set.
            # race_select simulates the window where a tick's SELECT ran BEFORE a
            # concurrent tick committed its claim — the row still appears here.
            self._result = [
                {"queue_id": qid, "spec_ref": spec}
                for qid, spec in self.db.queue
                if qid not in self.db.posted or qid in self.db.race_select
            ]
        elif "FROM bug_reports WHERE id" in s:
            bug_id = params[0]
            self._result = [self.db.bugs.get(bug_id)] if bug_id in self.db.bugs else [None]
        elif "INSERT INTO bug_report_channel_posts" in s:
            # claim-first: ON CONFLICT DO NOTHING RETURNING queue_id — a row already
            # claimed returns nothing (the loser); a fresh claim records + returns the id.
            qid = params[0]
            if qid in self.db.posted:
                self._result = []
            else:
                self.db.posted[qid] = {"bug_id": params[1], "note": params[2], "tg_out_id": None}
                self._result = [{"queue_id": qid}]
        elif "UPDATE bug_report_channel_posts SET tg_out_id" in s:
            tg_id, qid = params
            if qid in self.db.posted:
                self.db.posted[qid]["tg_out_id"] = tg_id
            self._result = []
        elif "DELETE FROM bug_report_channel_posts" in s:
            self.db.posted.pop(params[0], None)
            self._result = []
        elif "INSERT INTO agent_messages" in s:
            self.db.alerts.append(params)
            self._result = []
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


class _FakeConn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        self.db.commits += 1


class _FakeDB:
    def __init__(self, queue, bugs):
        self.queue = queue            # list of (queue_id, spec_ref)
        self.bugs = bugs              # {bug_id: {page_url, description}}
        self.posted = {}              # queue_id -> marker row
        self.alerts = []              # agent_messages inserts
        self.race_select = set()      # queue_ids SELECT returns even if already claimed (race sim)
        self.commits = 0


@pytest.fixture
def wired(monkeypatch):
    """Patch _dsn + psycopg.connect; return a factory that installs a given _FakeDB."""
    def install(db, enqueue_calls, raise_on_enqueue=False):
        monkeypatch.setattr(brc, "_dsn", lambda: "postgresql://fake")
        monkeypatch.setattr(brc.psycopg, "connect", lambda *a, **k: _FakeConn(db))

        def _fake_enqueue(channel, text=None, **k):
            enqueue_calls.append((channel, text))
            if raise_on_enqueue:
                raise RuntimeError("telegram down")
            return 9000 + len(enqueue_calls)

        monkeypatch.setattr(brc.tg_out, "enqueue", _fake_enqueue)
        return db
    return install


# ── Masking / compose ────────────────────────────────────────────────────────
def test_mask_nric():
    assert brc._mask("donor S1234567A complained") == "donor S****567A complained"
    assert brc._mask("T7654321Z and F1112223D") == "T****321Z and F****223D"
    assert brc._mask("no id here") == "no id here"


def test_compose_truncates_and_masks():
    out = brc.compose("/dashboard/people", "NRIC S1234567A shows " + "x" * 300)
    assert out.startswith('🐞 Logged issue (via the in-app Bug button) — Page: /dashboard/people.')
    assert "S****567A" in out and "S1234567A" not in out
    assert "…" in out  # truncated
    # the quoted description part stays within the cap (+ ellipsis)
    quoted = out.split('"', 1)[1].rsplit('"', 1)[0]
    assert len(quoted) <= brc._MAX_DESC + 1


def test_compose_unknown_page():
    assert "Page: (unknown page)." in brc.compose(None, "something broke")


# ── Post-once / idempotency ────────────────────────────────────────────────
def test_posts_once_then_idempotent(wired):
    db = _FakeDB(
        queue=[(101, "bug-report:11111111-1111-4111-8111-111111111111")],
        bugs={"11111111-1111-4111-8111-111111111111": {"page_url": "/dashboard", "description": "bank import does not load"}},
    )
    calls = []
    wired(db, calls)

    rc1 = brc.sweep(fire=True)
    assert rc1 == 0
    assert len(calls) == 1                      # posted exactly once
    assert 101 in db.posted                     # marker stamped
    assert db.posted[101]["tg_out_id"] == 9001

    # second run: the marked row is excluded -> no re-post
    rc2 = brc.sweep(fire=True)
    assert rc2 == 0
    assert len(calls) == 1                       # NOT called again (no double-post)


def test_dry_run_posts_nothing(wired):
    db = _FakeDB(
        queue=[(102, "bug-report:22222222-2222-4222-8222-222222222222")],
        bugs={"22222222-2222-4222-8222-222222222222": {"page_url": "/x", "description": "y"}},
    )
    calls = []
    wired(db, calls)
    brc.sweep(fire=False)
    assert calls == [] and db.posted == {}       # nothing sent, nothing stamped


def test_skip_missing_bug_row_marks_handled(wired):
    db = _FakeDB(queue=[(103, "bug-report:deadbeef-dead-4ead-8ead-deaddeaddead")], bugs={})
    calls = []
    wired(db, calls)
    rc = brc.sweep(fire=True)
    assert rc == 0
    assert calls == []                           # nothing posted
    assert 103 in db.posted                       # but marked, so it won't spin forever


def test_enqueue_failure_fails_loud_and_does_not_stamp(wired):
    db = _FakeDB(
        queue=[(104, "bug-report:33333333-3333-4333-8333-333333333333")],
        bugs={"33333333-3333-4333-8333-333333333333": {"page_url": "/p", "description": "boom"}},
    )
    calls = []
    wired(db, calls, raise_on_enqueue=True)
    rc = brc.sweep(fire=True)
    assert rc == 1                                # non-zero exit on error
    assert 104 not in db.posted                    # NOT stamped -> retried next tick
    # fail-loud: exactly one console alert, its body naming the failed row
    assert len(db.alerts) == 1
    assert "queue 104" in db.alerts[0][1]


def test_concurrent_claim_loser_does_not_enqueue(wired):
    """cc-quality #57189 LOW: if a concurrent tick already claimed the row (marker
    present) but this tick's stale SELECT still returns it, the claim-first INSERT
    returns no row and this tick must NOT enqueue (no duplicate Telegram send)."""
    qid = 105
    db = _FakeDB(
        queue=[(qid, "bug-report:44444444-4444-4444-8444-444444444444")],
        bugs={"44444444-4444-4444-8444-444444444444": {"page_url": "/z", "description": "dup"}},
    )
    db.posted[qid] = {"bug_id": None, "note": None, "tg_out_id": 1}  # claimed by the other tick
    db.race_select.add(qid)                                          # but our SELECT still sees it
    calls = []
    wired(db, calls)
    rc = brc.sweep(fire=True)
    assert rc == 0
    assert calls == []            # lost the claim -> did NOT enqueue (no double-send)
