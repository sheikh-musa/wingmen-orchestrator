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
