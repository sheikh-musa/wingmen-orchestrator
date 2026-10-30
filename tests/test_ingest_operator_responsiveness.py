"""Musa op#36085/36087/36098, 2026-10-30: 'the console must be always on, always
snappy.' An operator message got only the canned auto-ack, his URGENT reply did
not interrupt anything, and no check of the inbox happened for minutes. These
tests cover the three code-level pieces that close that gap in
nervous_system/ingest.py: (1) nudge_session's Escape-first preemption on
urgent=True, scoped to ESCAPE_PREEMPT_TARGETS only; (2) the tighter
OPERATOR_ACK_AFTER_SEC/OPERATOR_MAX_DEFER_SEC applied only to
OPERATOR_CHANNEL_TAGS, leaving client-channel timing untouched; (3)
_live_status_suffix's best-effort live text, with a hard cutoff so it never
presents stale activity as current."""
from datetime import datetime, timedelta, timezone

import pytest

from nervous_system import ingest


# ── nudge_session: Escape-first preemption ──────────────────────────────────

class _FakeRun:
    """Records every subprocess.run([tmux, ...]) call's argv; always 'succeeds'."""
    def __init__(self):
        self.calls = []
    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        class _R: returncode = 0
        return _R()


@pytest.fixture
def fake_tmux(monkeypatch):
    run = _FakeRun()
    monkeypatch.setattr(ingest.subprocess, "run", run)
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ingest.fire_window, "is_held", lambda *_: False)
    monkeypatch.setattr(ingest, "ESCAPE_PREEMPT_TARGETS", {"nazim"})
    return run


def _sent_keys(run, target):
    """The literal 2nd-to-last send-keys argument for each send-keys call to target."""
    return [c[-1] for c in run.calls if c[:2] == [ingest.shutil.which("tmux") or "/opt/homebrew/bin/tmux", "send-keys"] and f"={target}:0.0" in c]


def test_urgent_on_escape_eligible_target_sends_escape_first(fake_tmux):
    ok = ingest.nudge_session("nazim", "nazim-console", 2, urgent=True)
    assert ok is True
    sent = _sent_keys(fake_tmux, "nazim")
    assert sent[0] == "Escape", f"Escape must be the first key sent, got {sent}"
    assert "C-u" in sent and "Enter" in sent


def test_urgent_on_non_eligible_target_does_not_send_escape(fake_tmux):
    ok = ingest.nudge_session("orch", "orch-channel", 2, urgent=True)
    assert ok is True
    sent = _sent_keys(fake_tmux, "orch")
    assert "Escape" not in sent, "orch is not in ESCAPE_PREEMPT_TARGETS -- must stay gated"


def test_non_urgent_on_eligible_target_does_not_send_escape(fake_tmux):
    ok = ingest.nudge_session("nazim", "nazim-console", 2, urgent=False)
    assert ok is True
    sent = _sent_keys(fake_tmux, "nazim")
    assert "Escape" not in sent, "only urgent=True preempts, never a default-priority nudge"


# ── operator-channel SLA tightening ──────────────────────────────────────────

class _Cur:
    def __init__(self, rows, inserted):
        self.rows = list(rows)
        self.inserted = inserted
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        if sql.startswith("INSERT INTO tg_out"):
            self.inserted.append(params)
    def fetchone(self): return self.rows.pop(0)


class _Conn:
    """Feeds reassure_if_unhandled: one unhandled row `age_s` seconds old, chat
    not a group (so the group-addressing branch is skipped), and no prior ack
    in tg_out. `inserted` collects every tg_out INSERT's params."""
    def __init__(self, age_s, over_threshold):
        oldest = datetime.now(timezone.utc) - timedelta(seconds=age_s)
        self.inserted = []
        self._rows = [
            (1, oldest, oldest, None),   # count, oldest, newest, chat_id (None => not a group)
            (over_threshold,),           # now() - oldest > threshold
            None,                       # tg_out dedup: no existing ack row (only reached if over threshold)
        ]
    def cursor(self):
        return _Cur(self._rows, self.inserted)
    def commit(self): pass


class _Ch:
    def __init__(self, channel_tag, mode="agent-session", inject_target="orch", key=None):
        self.channel_tag = channel_tag
        self.key = key or channel_tag
        self.mode = mode
        self.inject_target = inject_target


@pytest.fixture
def no_live_status(monkeypatch):
    # Keep these tests focused on timing, not the live-status text.
    monkeypatch.setattr(ingest, "_live_status_suffix", lambda conn, target: "")
    monkeypatch.setattr(ingest, "SUPPRESS_CLIENT_ACK_TAGS", set())


def test_operator_channel_acks_before_client_threshold_is_reached(no_live_status):
    """At an age between OPERATOR_ACK_AFTER_SEC and ACK_AFTER_SEC, the operator
    channel must ack (tighter bar) while a client channel at the same age must not."""
    age = (ingest.OPERATOR_ACK_AFTER_SEC + ingest.ACK_AFTER_SEC) // 2
    assert ingest.OPERATOR_ACK_AFTER_SEC < age < ingest.ACK_AFTER_SEC

    conn = _Conn(age, over_threshold=True)
    ingest.reassure_if_unhandled(conn, _Ch("orch-channel"))
    assert len(conn.inserted) == 1, "operator channel at this age must get a reassurance ack"


def test_client_channel_same_age_does_not_yet_ack(no_live_status):
    age = (ingest.OPERATOR_ACK_AFTER_SEC + ingest.ACK_AFTER_SEC) // 2
    conn = _Conn(age, over_threshold=False)  # client's own ACK_AFTER_SEC not yet reached
    ingest.reassure_if_unhandled(conn, _Ch("some-client-channel"))
    assert conn.inserted == [], "client channel at this age must stay silent (its own bar is higher)"


def test_operator_channel_tags_cover_both_real_operator_channels():
    assert ingest.OPERATOR_CHANNEL_TAGS == {"orch-channel", "nazim-console"}


def test_operator_thresholds_are_tighter_than_client_thresholds():
    assert ingest.OPERATOR_ACK_AFTER_SEC < ingest.ACK_AFTER_SEC
    assert ingest.OPERATOR_MAX_DEFER_SEC < ingest.MAX_DEFER_SEC


# ── _live_status_suffix ──────────────────────────────────────────────────────

class _StatusCur:
    def __init__(self, row):
        self.row = row
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): pass
    def fetchone(self): return self.row


class _StatusConn:
    def __init__(self, row):
        self.row = row
    def cursor(self):
        return _StatusCur(self.row)


def test_live_status_suffix_fresh_row_is_included():
    conn = _StatusConn(("triaged op#36085", 42))
    suffix = ingest._live_status_suffix(conn, "orch")
    assert "triaged op#36085" in suffix


def test_live_status_suffix_stale_row_is_empty():
    conn = _StatusConn(("ancient status", 3600))  # > 1800s cutoff
    assert ingest._live_status_suffix(conn, "orch") == ""


def test_live_status_suffix_no_row_is_empty():
    conn = _StatusConn(None)
    assert ingest._live_status_suffix(conn, "orch") == ""


def test_live_status_suffix_unknown_target_is_empty():
    conn = _StatusConn(("whatever", 1))
    assert ingest._live_status_suffix(conn, "some-lane-not-a-body") == ""


def test_live_status_suffix_db_error_is_swallowed():
    class _Boom:
        def cursor(self):
            raise RuntimeError("db down")
    assert ingest._live_status_suffix(_Boom(), "orch") == ""
