"""Tests for scripts/live_checkout_branch_watchdog.py — the sustained-drift backstop
for scripts/git-hooks/post-checkout (bus #44681).

Covers the PURE decision core (evaluate: on_branch/grace/alert transitions, the
off_branch_since clock, boundary at grace_s), the PURE alert message, and run() wiring
(alerts once sustained, grace before that, re-page cooldown, dry-run is inert, a clean
DB read-miss never asserts a verdict). No real DB, no real git repo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import live_checkout_branch_watchdog as lcw  # noqa: E402

GRACE = lcw.GRACE_S
EXPECTED = lcw.EXPECTED_BRANCH


# ── PURE core ────────────────────────────────────────────────────────────────
def test_on_expected_branch_is_healthy():
    verdict, since = lcw.evaluate(EXPECTED, off_branch_since=None, now=1000)
    assert verdict == "on_branch"
    assert since is None


def test_on_expected_branch_clears_existing_tracking():
    # even if off_branch_since was set from a PRIOR scan, landing back on EXPECTED clears it
    verdict, since = lcw.evaluate(EXPECTED, off_branch_since=500, now=1000)
    assert verdict == "on_branch" and since is None


def test_first_off_branch_scan_starts_the_clock_and_is_grace():
    verdict, since = lcw.evaluate("some-other-branch", off_branch_since=None, now=1000)
    assert verdict == "grace"
    assert since == 1000  # clock starts NOW, not in the past — a single scan never alerts


def test_off_branch_under_grace_window_stays_grace():
    verdict, since = lcw.evaluate("some-other-branch", off_branch_since=1000, now=1000 + GRACE - 1)
    assert verdict == "grace"
    assert since == 1000


def test_off_branch_at_exactly_grace_alerts():
    # boundary: age == grace_s is sustained enough (>=, not >)
    verdict, since = lcw.evaluate("some-other-branch", off_branch_since=1000, now=1000 + GRACE)
    assert verdict == "alert"
    assert since == 1000


def test_off_branch_past_grace_alerts():
    verdict, since = lcw.evaluate("some-other-branch", off_branch_since=1000, now=1000 + GRACE + 500)
    assert verdict == "alert"


def test_detached_head_counts_as_off_branch():
    verdict, _ = lcw.evaluate("HEAD", off_branch_since=None, now=1000)
    assert verdict == "grace"


# ── PURE alert message ───────────────────────────────────────────────────────
def test_alert_message_shape():
    subj, body = lcw.alert_message("some-other-branch", 300)
    assert subj.startswith(lcw.SUBJECT_PREFIX)
    assert "some-other-branch" in subj and "300s" in subj
    low = body.lower()
    assert "commitment sweeper" in low
    assert f"checkout {EXPECTED}" in body
    assert "did not touch" in low  # detect-only framing


# ── run() wiring ─────────────────────────────────────────────────────────────
class _Cur:
    def __init__(self):
        self.inserts = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT"):
            self.inserts.append(params)


class _Conn:
    def __init__(self, cur):
        self._cur = cur
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1


def _wire(monkeypatch, branch, *, state=None, saved=None):
    monkeypatch.setattr(lcw, "_read_branch", lambda: branch)
    monkeypatch.setattr(lcw, "_load_state", lambda: dict(state or {}))
    monkeypatch.setattr(lcw, "_save_state", lambda st: (saved.update(st) if saved is not None else None))
    cur = _Cur()
    return cur, _Conn(cur)


def test_run_healthy_branch_alerts_nothing(monkeypatch):
    saved = {}
    cur, conn = _wire(monkeypatch, EXPECTED, saved=saved)
    lcw.run(conn, dry=False)
    assert cur.inserts == []
    assert "off_branch_since" not in saved


def test_run_first_drift_scan_is_grace_not_alert(monkeypatch):
    saved = {}
    cur, conn = _wire(monkeypatch, "some-other-branch", saved=saved)
    lcw.run(conn, dry=False)
    assert cur.inserts == []
    assert "off_branch_since" in saved  # tracking started


def test_run_sustained_drift_alerts(monkeypatch):
    saved = {}
    old_since = int(__import__("time").time()) - GRACE - 10
    cur, conn = _wire(monkeypatch, "some-other-branch",
                       state={"off_branch_since": old_since}, saved=saved)
    lcw.run(conn, dry=False)
    assert len(cur.inserts) == 1
    subject = cur.inserts[0][0]
    assert subject.startswith(lcw.SUBJECT_PREFIX)
    assert saved.get("last_alert_at")


def test_run_repage_cooldown_suppresses_duplicate_alert(monkeypatch):
    now = int(__import__("time").time())
    saved = {}
    cur, conn = _wire(monkeypatch, "some-other-branch",
                       state={"off_branch_since": now - GRACE - 10, "last_alert_at": now - 60},
                       saved=saved)
    lcw.run(conn, dry=False)
    assert cur.inserts == []  # just alerted a minute ago, well under REPAGE_S


def test_run_dry_run_never_inserts_or_saves(monkeypatch):
    old_since = int(__import__("time").time()) - GRACE - 10
    saved = {}
    cur, conn = _wire(monkeypatch, "some-other-branch",
                       state={"off_branch_since": old_since}, saved=saved)
    lcw.run(conn, dry=True)
    assert cur.inserts == []
    assert saved == {}


def test_run_read_miss_never_asserts_a_verdict(monkeypatch):
    saved = {}
    cur, conn = _wire(monkeypatch, None, state={"off_branch_since": 1}, saved=saved)
    lcw.run(conn, dry=False)
    assert cur.inserts == []
    assert "_heartbeat_epoch" in saved
    # a read-miss does not clear or alter existing off_branch_since tracking
    assert "off_branch_since" not in saved or saved.get("off_branch_since") == 1


def test_run_back_on_branch_clears_episode(monkeypatch):
    saved = {}
    cur, conn = _wire(monkeypatch, EXPECTED,
                       state={"off_branch_since": 1, "last_alert_at": 2}, saved=saved)
    lcw.run(conn, dry=False)
    assert cur.inserts == []
    assert "off_branch_since" not in saved
    assert "last_alert_at" not in saved
