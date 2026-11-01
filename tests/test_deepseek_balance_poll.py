"""deepseek_balance_poll.py -- previously untested (op#35929/35930 zero-waste
audit found the $/day cap logic already shipped live, PR#630, but with no
test coverage). Covers _total_usd, _trailing_24h_spend, and main()'s two
alert triggers (floor + daily budget), with vault/HTTP/bus boundaries
monkeypatched so nothing touches a real key, a real HTTP endpoint, or the
real bus.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import scripts.deepseek_balance_poll as d


@pytest.fixture(autouse=True)
def _isolated_alert_state(tmp_path, monkeypatch):
    """Every test gets its own alert-dedup state file.

    Without this the page-dedup would (a) write the LIVE state file from a test run and
    (b) make the tests order-dependent — the first main() call would record today's date
    and silently suppress every later test's page. Test isolation here is load-bearing,
    not hygiene.
    """
    monkeypatch.setattr(d, "ALERT_STATE_PATH", tmp_path / "alert_state.json")


# --------------------------------------------------------------------------
# _total_usd
# --------------------------------------------------------------------------

def test_total_usd_picks_usd_currency_entry():
    payload = {"balance_infos": [
        {"currency": "CNY", "total_balance": "999"},
        {"currency": "USD", "total_balance": "46.68"},
    ]}
    assert d._total_usd(payload) == 46.68


def test_total_usd_falls_back_to_first_entry_if_no_usd():
    payload = {"balance_infos": [{"currency": "EUR", "total_balance": "12.5"}]}
    assert d._total_usd(payload) == 12.5


def test_total_usd_raises_on_empty_balance_infos():
    with pytest.raises(RuntimeError):
        d._total_usd({"balance_infos": []})


# --------------------------------------------------------------------------
# _trailing_24h_spend
# --------------------------------------------------------------------------

def _write_history(tmp_path, monkeypatch, rows):
    state_path = tmp_path / "deepseek_balance_history.jsonl"
    state_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(d, "STATE_PATH", state_path)
    return state_path


def test_trailing_24h_spend_uses_baseline_24h_ago(tmp_path, monkeypatch):
    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
    rows = [
        {"ts": (now - timedelta(hours=30)).isoformat(), "balance_usd": 60.0},  # older than 24h
        {"ts": (now - timedelta(hours=23)).isoformat(), "balance_usd": 50.0},  # baseline (newest <=24h-ago... )
        {"ts": (now - timedelta(hours=1)).isoformat(), "balance_usd": 42.0},
    ]
    _write_history(tmp_path, monkeypatch, rows)
    spend = d._trailing_24h_spend(now.isoformat(), latest_balance=42.0)
    # baseline picked = newest row with ts <= cutoff (24h ago) -> the 23h-ago... actually
    # cutoff = now-24h; rows with ts <= cutoff are only the 30h-ago row (23h-ago is AFTER cutoff).
    assert spend == pytest.approx(60.0 - 42.0)


def test_trailing_24h_spend_falls_back_to_oldest_row_when_history_shorter_than_24h(tmp_path, monkeypatch):
    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
    rows = [
        {"ts": (now - timedelta(hours=2)).isoformat(), "balance_usd": 50.0},
        {"ts": (now - timedelta(hours=1)).isoformat(), "balance_usd": 48.0},
    ]
    _write_history(tmp_path, monkeypatch, rows)
    spend = d._trailing_24h_spend(now.isoformat(), latest_balance=48.0)
    assert spend == pytest.approx(50.0 - 48.0)


def test_trailing_24h_spend_missing_file_returns_zero_never_a_guess(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "does_not_exist.jsonl")
    assert d._trailing_24h_spend(datetime.now(timezone.utc).isoformat(), latest_balance=10.0) == 0.0


def test_trailing_24h_spend_empty_history_returns_zero(tmp_path, monkeypatch):
    _write_history(tmp_path, monkeypatch, [])
    assert d._trailing_24h_spend(datetime.now(timezone.utc).isoformat(), latest_balance=10.0) == 0.0


# --------------------------------------------------------------------------
# main() -- both alert triggers, via monkeypatched boundaries
# --------------------------------------------------------------------------

class _Calls:
    def __init__(self):
        self.alerts = []
        self.budget_alerts = []


def test_main_fires_floor_alert_when_below_floor(tmp_path, monkeypatch):
    calls = _Calls()
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "hist.jsonl")
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "20.00"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal) or True)
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend) or True)
    rc = d.main()
    assert rc == 0
    assert calls.alerts == [20.0]  # below ALERT_FLOOR_USD (40.0)


def test_main_no_floor_alert_when_above_floor(tmp_path, monkeypatch):
    calls = _Calls()
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "hist.jsonl")
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "46.68"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal) or True)
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend) or True)
    rc = d.main()
    assert rc == 0
    assert calls.alerts == []


def test_main_fires_budget_alert_when_24h_spend_exceeds_cap(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    hist = tmp_path / "hist.jsonl"
    # 24h ago balance was 60 -> today's reading of 40 is a $20 spend, over the $7.80/day cap.
    hist.write_text(json.dumps({"ts": (now - timedelta(hours=25)).isoformat(), "balance_usd": 60.0}) + "\n")
    calls = _Calls()
    monkeypatch.setattr(d, "STATE_PATH", hist)
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "40.00"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal) or True)
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend) or True)
    rc = d.main()
    assert rc == 0
    assert calls.budget_alerts == pytest.approx([20.0])


def test_main_no_budget_alert_under_cap(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    hist = tmp_path / "hist.jsonl"
    hist.write_text(json.dumps({"ts": (now - timedelta(hours=25)).isoformat(), "balance_usd": 46.70}) + "\n")
    calls = _Calls()
    monkeypatch.setattr(d, "STATE_PATH", hist)
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "46.68"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal) or True)
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend) or True)
    rc = d.main()
    assert rc == 0
    assert calls.budget_alerts == []


def test_main_fails_loud_never_silent_on_fetch_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "hist.jsonl")
    def boom():
        raise RuntimeError("vault miss")
    monkeypatch.setattr(d, "fetch_key", boom)
    rc = d.main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAILED" in err and "vault miss" in err


# --------------------------------------------------------------------------
# Once-per-day page dedup (orch-console #89707)
# --------------------------------------------------------------------------

def test_second_poll_on_the_same_day_does_not_page_again(tmp_path, monkeypatch):
    """This job runs HOURLY. A condition that persists all day used to page every hour;
    on 2026-11-01 the floor paged again while the balance was already under active
    management, carrying no new fact."""
    calls = _Calls()
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "hist.jsonl")
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "20.00"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal) or True)
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend) or True)
    assert d.main() == 0
    assert d.main() == 0
    assert calls.alerts == [20.0]


def test_every_poll_still_lands_in_history_even_when_the_page_is_deduped(tmp_path, monkeypatch):
    """The dedup is on the PAGE, never on the measurement — the spend curve and the 24h
    figure both read this file, so suppressing the write would blind the very thing the
    alert exists to report."""
    hist = tmp_path / "hist.jsonl"
    monkeypatch.setattr(d, "STATE_PATH", hist)
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "20.00"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: True)
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: True)
    d.main(); d.main()
    assert len(hist.read_text().strip().splitlines()) == 2


def test_a_new_day_pages_again(tmp_path, monkeypatch):
    """A balance stuck below the floor must still surface daily — dedup ratchets to one
    page per day, it does not go quiet."""
    state = tmp_path / "alert_state.json"
    assert d._already_alerted_today("balance_floor", "2026-11-01", state) is False
    d._record_alert("balance_floor", "2026-11-01", state)
    assert d._already_alerted_today("balance_floor", "2026-11-01", state) is True
    assert d._already_alerted_today("balance_floor", "2026-11-02", state) is False


def test_kinds_are_deduped_independently(tmp_path, monkeypatch):
    """The floor page and the daily-budget page are different findings; one must not
    silence the other."""
    state = tmp_path / "alert_state.json"
    d._record_alert("balance_floor", "2026-11-01", state)
    assert d._already_alerted_today("daily_budget", "2026-11-01", state) is False


def test_unreadable_state_fails_toward_paging(tmp_path, monkeypatch):
    """The only thing that may suppress a money page is a clean matching record. Absent,
    corrupt or wrong-shaped state all mean 'cannot prove we already told them'."""
    missing = tmp_path / "nope.json"
    assert d._already_alerted_today("balance_floor", "2026-11-01", missing) is False

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert d._already_alerted_today("balance_floor", "2026-11-01", corrupt) is False

    wrong_shape = tmp_path / "list.json"
    wrong_shape.write_text('["2026-11-01"]')
    assert d._already_alerted_today("balance_floor", "2026-11-01", wrong_shape) is False


def test_a_corrupt_state_file_does_not_lose_the_earlier_day(tmp_path, monkeypatch):
    """Recording over a corrupt file must not crash the poll: the page has already gone
    out, and a monitor that dies after paging looks identical to one that never paged."""
    state = tmp_path / "alert_state.json"
    state.write_text("{not json")
    d._record_alert("balance_floor", "2026-11-01", state)
    assert d._already_alerted_today("balance_floor", "2026-11-01", state) is True


def test_a_failed_page_is_not_recorded_so_the_next_poll_retries(tmp_path, monkeypatch):
    """THE DEFECT THIS GUARDS (cc-quality #89717). bus_send exits non-zero on a transient
    condition -- DB blip, pooler circuit breaker, soak refusal. Recording the day as
    'already paged' on a send that never went out silences the floor until midnight: the
    pre-dedup code would simply have retried on the next hourly poll, so unconditionally
    recording is a NEW missed-page path, in the one direction this module must never fail.
    """
    calls = _Calls()
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "hist.jsonl")
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "20.00"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal) or False)  # bus_send failed
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: True)

    d.main()                                    # page attempt fails, must NOT be recorded
    assert d._already_alerted_today("balance_floor", d.datetime.now(timezone.utc).date().isoformat()) is False

    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal) or True)   # next hour, bus is back
    d.main()
    assert calls.alerts == [20.0, 20.0], "the retry must actually page"


def test_a_raising_page_does_not_record_the_day_as_covered(tmp_path, monkeypatch, capsys):
    """A send that raises must not leave a 'paged today' marker behind either."""
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "hist.jsonl")
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "20.00"}]})
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: True)

    def _boom(_bal):
        raise OSError("bus_send missing")
    monkeypatch.setattr(d, "send_alert", _boom)
    with pytest.raises(OSError):
        d.main()
    assert d._already_alerted_today("balance_floor", d.datetime.now(timezone.utc).date().isoformat()) is False


def test_a_successful_page_is_recorded(tmp_path, monkeypatch):
    """The guard on the guard: gating the record on send success must not stop a GOOD page
    from being deduped."""
    calls = _Calls()
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "hist.jsonl")
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "20.00"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal) or True)
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: True)
    d.main(); d.main()
    assert calls.alerts == [20.0]
