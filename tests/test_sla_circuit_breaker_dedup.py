"""The operator-facing circuit-breaker page must not spam the phone (#25147): it fired
every ~90s run while tripped. circuit_breaker_should_page dedups it — page once per
episode, then honor a re-page backoff."""
from scripts import priority_sla_watchdog as W


def test_first_trip_pages():
    assert W.circuit_breaker_should_page({"last_paged_ts": 0}, 1_000_000.0, 60) is True


def test_within_backoff_suppresses():
    now = 1_000_000.0
    assert W.circuit_breaker_should_page({"last_paged_ts": now - 600}, now, 60) is False  # 10m ago


def test_after_backoff_repages():
    now = 1_000_000.0
    assert W.circuit_breaker_should_page({"last_paged_ts": now - 4200}, now, 60) is True  # 70m ago


def test_missing_or_empty_state_pages():
    assert W.circuit_breaker_should_page({}, 1_000_000.0, 60) is True
    assert W.circuit_breaker_should_page(None, 1_000_000.0, 60) is True
