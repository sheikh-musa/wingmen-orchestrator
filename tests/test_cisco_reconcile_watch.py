"""Tests for scripts/cisco_reconcile_watch.py — the pure stuck-row classifier.

The load-bearing logic: is_stuck / select_stuck must flag a coin-deposit ONLY when it is in a
NON-TERMINAL status (draft/count_approved) AND has aged past the threshold — never a reconciled
row, never a fresh in-progress row. No DB, no PII — pure functions.
"""
import pytest

from scripts import cisco_reconcile_watch as w

H = 3600.0


@pytest.mark.parametrize("status", ["draft", "count_approved"])
def test_nonterminal_past_threshold_is_stuck(status):
    assert w.is_stuck(status, 3 * H, stuck_hours=2.5) is True


@pytest.mark.parametrize("status", ["draft", "count_approved"])
def test_nonterminal_within_threshold_not_stuck(status):
    # fresh in-progress row — normal, not stuck
    assert w.is_stuck(status, 1 * H, stuck_hours=2.5) is False


def test_reconciled_is_never_stuck():
    # terminal-clean, even if 'old' — a reconciled row is the SUCCESS case, never stuck
    assert w.is_stuck("reconciled", 99 * H, stuck_hours=2.5) is False


def test_unknown_status_not_stuck():
    assert w.is_stuck("some_other_state", 99 * H, stuck_hours=2.5) is False


def test_none_age_fails_safe_not_stuck():
    assert w.is_stuck("draft", None, stuck_hours=2.5) is False


def test_at_threshold_is_not_stuck():
    # exactly at threshold is not > threshold — no false-fire on the boundary
    assert w.is_stuck("count_approved", 2.5 * H, stuck_hours=2.5) is False


def test_select_stuck_picks_only_stuck_rows():
    rows = [
        ("uuid-a", "draft", 3 * H),           # stuck
        ("uuid-b", "count_approved", 5 * H),  # stuck
        ("uuid-c", "draft", 0.5 * H),         # fresh -> not stuck
        ("uuid-d", "reconciled", 9 * H),      # done -> not stuck
        ("uuid-e", "draft", None),            # unmeasurable age -> fail-safe not stuck
    ]
    got = [r[0] for r in w.select_stuck(rows, stuck_hours=2.5)]
    assert got == ["uuid-a", "uuid-b"]


def test_select_stuck_empty():
    assert w.select_stuck([], stuck_hours=2.5) == []


@pytest.mark.parametrize("hours,age_h,expected", [
    (3.0, 2.9, False),
    (3.0, 3.1, True),
    (2.0, 2.5, True),
])
def test_threshold_configurable(hours, age_h, expected):
    assert w.is_stuck("draft", age_h * H, stuck_hours=hours) is expected


def test_non_terminal_set_is_the_documented_lifecycle():
    # guard: the non-terminal set must be exactly the pre-reconcile states
    assert w.NON_TERMINAL == {"draft", "count_approved"}
    assert w.TERMINAL_CLEAN == "reconciled"


def test_safe_columns_never_include_pii():
    # enforce-in-code: the read allowlist must never carry a money/identity/reference/url column
    forbidden = {"internal_count", "certis_count", "net_deposit", "bank_confirmed_amount",
                 "certis_fee", "bank_charge", "total_fee", "preparer_id", "endorser_id",
                 "deposit_reference", "deposit_slip_url", "certis_receipt_url", "notes",
                 "certis_batch_ref", "certis_source_ref", "content_hash", "variance_reason"}
    assert set(w.SAFE_COLUMNS).isdisjoint(forbidden)
    assert set(w.SAFE_COLUMNS) == {"public_id", "status", "created_at", "updated_at"}


# ── retry-before-could-not-measure (port of ad38b99): a transient Supabase pooler-DNS blip on
#    the goumlyne connect must NOT trip the money-path dead-man; a PERSISTENT failure still does.
#    (2026-09-03: coord + orch-console verified the go-live blip was a transient pooler-DNS blip.)

class _Transient(Exception):
    """Stand-in for psycopg.OperationalError (DNS/connect blip)."""


def test_retry_recovers_after_transient_failures_without_raising():
    attempts = {"n": 0}
    slept: list[float] = []

    def op():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _Transient("could not translate host name pooler.supabase.com")
        return "ok"

    result = w._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=slept.append)
    assert result == "ok"
    assert attempts["n"] == 3
    assert slept == [0.01, 0.02]


def test_retry_reraises_after_exhaustion():
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        raise _Transient("host still unresolvable")

    with pytest.raises(_Transient, match="host still unresolvable"):
        w._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=lambda s: None)
    assert attempts["n"] == 3


def test_retry_does_not_retry_non_transient():
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        raise ValueError("bad query")

    with pytest.raises(ValueError, match="bad query"):
        w._retry(op, attempts=3, base_delay_s=0.01, retry_on=_Transient, sleep=lambda s: None)
    assert attempts["n"] == 1


# --- integration: run_once retry behaviour around the goumlyne connect ---

class _FakeCur:
    def execute(self, *a, **k):
        pass
    def fetchone(self):
        return (0,)          # count queries -> 0 (empty/live-waiting table)
    def fetchall(self):
        return []            # no non-terminal rows
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _FakeGoumlyneConn:
    def cursor(self):
        return _FakeCur()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _prep_run_once(monkeypatch):
    monkeypatch.setattr(w, "_load_dotenv_launchd", lambda: None)
    monkeypatch.setattr(w, "_dsn_goumlyne", lambda: "postgres://goumlyne-ignored")
    paged: list[str] = []
    monkeypatch.setattr(w, "_page_loud_cannot_measure",
                        lambda reason: (paged.append(reason) or 2))
    monkeypatch.setattr(w, "ENABLED", False)  # detect-only: no page-DB connection on the clean path
    return paged


def test_run_once_recovers_from_transient_goumlyne_blip(monkeypatch):
    """The incident: goumlyne connect raises OperationalError ONCE (pooler DNS blip) then
    succeeds. run_once must recover and NOT trip the could-not-measure dead-man."""
    import psycopg
    paged = _prep_run_once(monkeypatch)
    calls = {"n": 0}

    def fake_connect(dsn, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.OperationalError(
                "connection failed: could not translate host name "
                "aws-1-ap-southeast-1.pooler.supabase.com")
        return _FakeGoumlyneConn()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(w, "_sleep", lambda s: None)
    rc = w.run_once()
    assert calls["n"] == 2, "must retry the transient blip"
    assert paged == [], "a transient blip must NOT trip the money-path dead-man"
    assert rc == 0


def test_run_once_persistent_goumlyne_failure_still_pages_dead_man(monkeypatch):
    """A GENUINE persistent goumlyne outage must STILL page could-not-measure after the retry
    budget — retries buy a grace window, they never swallow a real money-path outage."""
    import psycopg
    paged = _prep_run_once(monkeypatch)

    def fake_connect(dsn, **k):
        raise psycopg.OperationalError("could not translate host name (persistent)")

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(w, "_sleep", lambda s: None)
    rc = w.run_once()
    assert len(paged) == 1, "persistent failure MUST page could-not-measure (dead-man preserved)"
    assert rc == 2
