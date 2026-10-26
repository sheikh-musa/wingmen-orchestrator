"""Unit tests for the "nothing goes dark" status-board DERIVATION (bus 77019,
Phase 1 — reports/nothing-goes-dark-design-20261026/design.md parts 1+5).

waiting_party / last_party_update / overdue are DERIVED per read, never stored,
so they can't go stale — same doctrine as the asks board's live status column
(build_fleet_asks_query). These tests pin the pure derivation rules; the SQL
union + endpoint passthrough live in test_app.py.
"""
from datetime import datetime, timedelta, timezone

from nervous_system.console import db

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat()


# ---- waiting_party: who the open item owes its next update to ---------------

def test_ask_waiting_party_by_surface():
    assert db.waiting_party_for_ask("operator") == "operator"
    assert db.waiting_party_for_ask("client-channel") == "client"
    # Unknown/NULL surface defaults to the operator — never silently drops a
    # client item's visibility axis, it just files it under the safe default.
    assert db.waiting_party_for_ask(None) == "operator"
    assert db.waiting_party_for_ask("whatever") == "operator"


def test_commitment_waiting_party_by_channel_tag():
    # Internal/operator channels → the operator is owed the update.
    for tag in (None, "", "nazim-console", "tmux-console", "fleet",
                "orch-channel", "operator-orch"):
        assert db.waiting_party_for_commitment(tag) == ("operator", None)
    # Any other tag names a CLIENT channel — the tag itself is the party name
    # (the design doc's hc #637 class: waiting_party=operator; here, client).
    assert db.waiting_party_for_commitment("gazzabyte-irsyad") == \
        ("client", "gazzabyte-irsyad")
    assert db.waiting_party_for_commitment("cosem-caai") == ("client", "cosem-caai")
    # whitespace never forks the key
    assert db.waiting_party_for_commitment("  gazzabyte-irsyad ") == \
        ("client", "gazzabyte-irsyad")


# ---- overdue: past a due date OR quiet past the party cadence ---------------

def test_overdue_past_due_wins_over_quiet():
    due = iso(NOW - timedelta(hours=1))
    fresh = iso(NOW - timedelta(minutes=5))
    assert db.derive_overdue(due, fresh, "operator", now=NOW) == (True, "past_due")


def test_overdue_quiet_operator_24h_cadence():
    assert db.derive_overdue(None, iso(NOW - timedelta(hours=23)),
                             "operator", now=NOW) == (False, None)
    assert db.derive_overdue(None, iso(NOW - timedelta(hours=25)),
                             "operator", now=NOW) == (True, "quiet")


def test_overdue_quiet_client_12h_cadence():
    assert db.derive_overdue(None, iso(NOW - timedelta(hours=11)),
                             "client", now=NOW) == (False, None)
    assert db.derive_overdue(None, iso(NOW - timedelta(hours=13)),
                             "client", now=NOW) == (True, "quiet")


def test_overdue_future_due_not_overdue():
    assert db.derive_overdue(iso(NOW + timedelta(days=2)),
                             iso(NOW - timedelta(hours=1)),
                             "operator", now=NOW) == (False, None)


def test_overdue_missing_last_update_is_quiet():
    # A NULL last-signal must not silently escape the clock — treat it as
    # "we have never updated this party" → overdue (the anti-dark default).
    assert db.derive_overdue(None, None, "operator", now=NOW) == (True, "quiet")
    assert db.derive_overdue(iso(NOW + timedelta(days=1)), None,
                             "operator", now=NOW) == (True, "quiet")


def test_overdue_accepts_datetime_and_date_only():
    assert db.derive_overdue(NOW - timedelta(hours=1),
                             NOW - timedelta(minutes=1),
                             "operator", now=NOW) == (True, "past_due")
    # date-only strings (defensive: any caller passing a DATE column) parse
    # as that day's UTC midnight — a due date of "yesterday" is past.
    assert db.derive_overdue("2026-10-25", iso(NOW - timedelta(minutes=1)),
                             "operator", now=NOW) == (True, "past_due")


def test_overdue_bad_strings_treated_as_quiet_never_crash():
    # garbage in → the safe "quiet" verdict, never an exception on the page
    assert db.derive_overdue("not-a-date", "also-garbage", "operator",
                             now=NOW) == (True, "quiet")


# ---- annotate: the per-row assembly over union rows -------------------------

def _ask_row(**over):
    row = {
        "kind": "ask", "id": 11, "title": "Silent client ask",
        "owner": "cc-irsyad", "status": "in_progress",
        "waiting_party_raw": "client-channel",
        "due_at": None, "last_update_at": None, "last_update_age_s": 13 * 3600,
    }
    row.update(over)
    return row


def test_annotate_status_rows_parties_and_ordering():
    rows = [
        _ask_row(last_update_at=iso(NOW - timedelta(hours=13))),  # client, quiet 13h
        _ask_row(id=12, title="Fresh operator ask", waiting_party_raw="operator",
                 last_update_at=iso(NOW - timedelta(hours=1)), last_update_age_s=3600),
        {
            "kind": "commitment", "id": 637, "title": "Monthly billing sweep",
            "owner": "cc-irsyad-coord", "status": "pending",
            "waiting_party_raw": None,
            "due_at": iso(NOW - timedelta(hours=2)),
            "last_update_at": iso(NOW - timedelta(minutes=10)),
            "last_update_age_s": 600,
        },
    ]
    out = db.annotate_status_rows(rows, now=NOW)
    by_title = {r["title"]: r for r in out}
    assert by_title["Silent client ask"]["waiting_party"] == "client"
    assert by_title["Silent client ask"]["client_name"] is None  # asks name no client
    assert by_title["Fresh operator ask"]["waiting_party"] == "operator"
    assert by_title["Monthly billing sweep"]["waiting_party"] == "operator"
    assert by_title["Monthly billing sweep"]["overdue"] is True
    assert by_title["Monthly billing sweep"]["overdue_reason"] == "past_due"
    # darkest first: both overdue rows above the fresh one
    assert [r["overdue"] for r in out] == [True, True, False]
    # provenance rides along (anti-dark: every item is clickable to its source)
    assert all(r["source"].startswith(("operator_asks#", "held_commitments#"))
               for r in out)


def test_annotate_fired_commitment_is_always_overdue():
    # 'fired' = the commitment sweeper escalated a BREACH (commitment_sweeper.py
    # fires on due passing and re-escalates until discharged). A fresh touch on
    # the row must not un-dark a live breach.
    rows = [{
        "kind": "commitment", "id": 700, "title": "Breach",
        "owner": "cc-cosem", "status": "fired",
        "waiting_party_raw": None,
        "due_at": iso(NOW - timedelta(hours=30)),
        "last_update_at": iso(NOW - timedelta(minutes=5)),
        "last_update_age_s": 300,
    }]
    out = db.annotate_status_rows(rows, now=NOW)
    assert out[0]["overdue"] is True
    assert out[0]["overdue_reason"] == "fired"
