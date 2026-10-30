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
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal))
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend))
    rc = d.main()
    assert rc == 0
    assert calls.alerts == [20.0]  # below ALERT_FLOOR_USD (40.0)


def test_main_no_floor_alert_when_above_floor(tmp_path, monkeypatch):
    calls = _Calls()
    monkeypatch.setattr(d, "STATE_PATH", tmp_path / "hist.jsonl")
    monkeypatch.setattr(d, "fetch_key", lambda: "fake-key")
    monkeypatch.setattr(d, "fetch_balance", lambda key: {"balance_infos": [{"currency": "USD", "total_balance": "46.68"}]})
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal))
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend))
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
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal))
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend))
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
    monkeypatch.setattr(d, "send_alert", lambda bal: calls.alerts.append(bal))
    monkeypatch.setattr(d, "send_budget_alert", lambda spend: calls.budget_alerts.append(spend))
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
