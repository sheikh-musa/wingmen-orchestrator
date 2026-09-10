"""Tests for scripts/channel_liveness_watchdog.py — rung-1 of the channel-liveness watchdog.

Covers the PURE decision core (evaluate_channel: triggers A/B, row_frozen deferral, ordering,
boundaries, NULL-last_ok safety), the PURE alert message, and run() wiring (alerts an error-storm/
stale channel, dedups, dry-run is inert, row_frozen is logged-not-alerted). No real DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import channel_liveness_watchdog as clw  # noqa: E402

STALE = clw.STALE_S
STORM = clw.ERROR_STORM
ROWSTALE = clw.ROW_STALE_S


# ── PURE core ────────────────────────────────────────────────────────────────
def test_healthy_channel_is_ok():
    assert clw.evaluate_channel(last_ok_age_s=10, consec_errors=0, updated_age_s=10)[0] == "ok"


def test_error_storm_fires_when_actively_polling():
    v, _ = clw.evaluate_channel(last_ok_age_s=30, consec_errors=STORM, updated_age_s=5)
    assert v == "error_storm"
    assert clw.evaluate_channel(last_ok_age_s=30, consec_errors=STORM - 1, updated_age_s=5)[0] == "ok"


def test_stale_poll_fires_on_old_success_while_polling():
    v, _ = clw.evaluate_channel(last_ok_age_s=STALE + 1, consec_errors=1, updated_age_s=5)
    assert v == "stale_poll"
    # exactly at the bar is NOT stale (strict >)
    assert clw.evaluate_channel(last_ok_age_s=STALE, consec_errors=1, updated_age_s=5)[0] == "ok"


def test_row_frozen_when_updated_at_stale_defers_not_alerts():
    # frozen row with a HIGH consec + OLD last_ok must NOT become error_storm/stale_poll —
    # its inputs are frozen/untrustworthy; it's row_frozen (deferred to trigger C).
    v, _ = clw.evaluate_channel(last_ok_age_s=99999, consec_errors=999, updated_age_s=ROWSTALE + 1)
    assert v == "row_frozen"
    assert v not in clw.ALERT_VERDICTS


def test_row_frozen_when_updated_age_none():
    assert clw.evaluate_channel(last_ok_age_s=None, consec_errors=0, updated_age_s=None)[0] == "row_frozen"


def test_null_last_ok_fresh_low_errors_is_ok_not_premature_stale():
    # a just-instrumented channel that hasn't succeeded YET but is only 1-2 errors in must NOT
    # false-fire stale_poll — error_storm catches it later as consec climbs.
    assert clw.evaluate_channel(last_ok_age_s=None, consec_errors=1, updated_age_s=5)[0] == "ok"


def test_null_last_ok_wedged_from_start_is_error_storm():
    v, _ = clw.evaluate_channel(last_ok_age_s=None, consec_errors=STORM, updated_age_s=5)
    assert v == "error_storm"


def test_row_frozen_takes_priority_over_error_storm():
    # ordering: even with consec >= storm, a frozen row is row_frozen (don't trust frozen consec)
    v, _ = clw.evaluate_channel(last_ok_age_s=10, consec_errors=STORM + 50, updated_age_s=ROWSTALE + 100)
    assert v == "row_frozen"


# ── PURE alert message ───────────────────────────────────────────────────────
def test_alert_message_shape():
    subj, body = clw.alert_message("gazzabyte-irsyad", "Sheikhs-Mini", "error_storm", "consec=9 ...")
    assert subj.startswith(f"{clw.SUBJECT_PREFIX} gazzabyte-irsyad@Sheikhs-Mini: error_storm")
    low = body.lower()
    assert "gazzabyte-irsyad" in body and "sheikhs-mini" in low
    assert "did not touch" in low                  # detect-only framing
    assert "restart will not fix" in low or "restart will not" in low  # the Errno-54 guidance


# ── run() wiring ─────────────────────────────────────────────────────────────
class _Cur:
    def __init__(self, dedup_hit=False):
        self.inserts = []
        self._dedup_hit = dedup_hit

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sql = sql
        if sql.strip().upper().startswith("INSERT"):
            self.inserts.append(params)

    def fetchone(self):
        # _alerted_today dedup query -> return a row iff dedup_hit
        return (1,) if self._dedup_hit else None

    def fetchall(self):
        return []

    @property
    def description(self):
        return []


class _Conn:
    def __init__(self, cur):
        self._cur = cur
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1


def _wire(monkeypatch, rows, *, dedup_hit=False, saved=None):
    monkeypatch.setattr(clw, "_fetch_rows", lambda cur: rows)
    monkeypatch.setattr(clw, "_load_state", lambda: {})
    monkeypatch.setattr(clw, "_save_state", lambda st: (saved.update(st) if saved is not None else None))
    cur = _Cur(dedup_hit=dedup_hit)
    return cur, _Conn(cur)


def test_run_alerts_an_error_storm_channel(monkeypatch):
    rows = [{"channel_key": "gazzabyte-irsyad", "host": "Sheikhs-Mini",
             "consec_errors": 9, "last_ok_age_s": 700, "updated_age_s": 4}]
    saved = {}
    cur, conn = _wire(monkeypatch, rows, saved=saved)
    clw.run(conn, dry=False)
    assert len(cur.inserts) == 1
    params = cur.inserts[0]
    assert f"{clw.SUBJECT_PREFIX} gazzabyte-irsyad@Sheikhs-Mini: error_storm" in params[0]
    assert "_heartbeat_epoch" in saved            # dead-man heartbeat stamped


def test_run_healthy_fleet_alerts_nothing(monkeypatch):
    rows = [{"channel_key": c, "host": "h", "consec_errors": 0, "last_ok_age_s": 5, "updated_age_s": 5}
            for c in ("a", "b", "c")]
    cur, conn = _wire(monkeypatch, rows)
    clw.run(conn, dry=False)
    assert cur.inserts == []


def test_run_dedups_within_day(monkeypatch):
    rows = [{"channel_key": "x", "host": "h", "consec_errors": 20, "last_ok_age_s": 900, "updated_age_s": 3}]
    cur, conn = _wire(monkeypatch, rows, dedup_hit=True)
    clw.run(conn, dry=False)
    assert cur.inserts == []                       # already alerted today -> no duplicate page


def test_run_dry_run_is_inert(monkeypatch):
    rows = [{"channel_key": "x", "host": "h", "consec_errors": 20, "last_ok_age_s": 900, "updated_age_s": 3}]
    saved = {}
    cur, conn = _wire(monkeypatch, rows, saved=saved)
    clw.run(conn, dry=True)
    assert cur.inserts == []                       # dry-run never inserts
    assert saved == {}                             # and never writes state


def test_run_row_frozen_is_logged_not_alerted(monkeypatch):
    rows = [{"channel_key": "disabled-or-dead", "host": "h",
             "consec_errors": 999, "last_ok_age_s": 99999, "updated_age_s": clw.ROW_STALE_S + 500}]
    cur, conn = _wire(monkeypatch, rows)
    clw.run(conn, dry=False)
    assert cur.inserts == []                       # frozen row is deferred to trigger C, never A/B-paged
