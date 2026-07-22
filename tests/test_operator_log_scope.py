"""test_operator_log_scope.py — fail-closed body-role scoping (FIX #6).

_channel_scope_sql() builds the SQL clause that keeps each orch BODY (hub vs
console/Nazim) reconciling ONLY its own operator surfaces. Before FIX #6 an
unrecognized/typo'd ORCH_BODY_ROLE fell through to "" (no filter) — so
mark_handled_through() would stamp EVERY channel's inbound handled in one call
(cross-body message loss; the 2026-07-05 incident, commit 3691cea).

These are PURE-LOGIC tests: they only monkeypatch ORCH_BODY_ROLE and call
_channel_scope_sql() — no live DB, no psycopg connection. They assert the legit
console/hub/empty behavior is unchanged and that an unknown role RAISES loudly
instead of silently producing an unscoped query.
"""
import pytest

from nervous_system import operator_log as ol


def _set_role(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    else:
        monkeypatch.setenv("ORCH_BODY_ROLE", value)


def test_console_role_scopes_to_console_surfaces(monkeypatch):
    _set_role(monkeypatch, "console")
    clause = ol._channel_scope_sql()
    # Nazim reconciles his own surfaces only.
    assert "channel='tmux-console'" in clause
    assert "tag='nazim-console'" in clause
    # Shared feeds are carved out (not a personal-DM nudge).
    assert "war-room" in clause and "hafiz-partner" in clause
    # It is a filter, never an empty (unscoped) clause.
    assert clause.strip() != ""
    assert clause.lstrip().startswith("AND")


def test_hub_role_scopes_away_other_bodies(monkeypatch):
    _set_role(monkeypatch, "hub")
    clause = ol._channel_scope_sql()
    # Hub sees everything EXCEPT the console surface and the other bodies' DMs.
    assert "channel<>'tmux-console'" in clause
    assert "tag IS DISTINCT FROM 'nazim-console'" in clause
    assert "tag IS DISTINCT FROM 'cai-channel'" in clause
    assert "war-room" in clause and "hafiz-partner" in clause
    assert clause.strip() != ""


def test_empty_role_is_legacy_unscoped(monkeypatch):
    # Unset (legacy single-body) behavior is SANCTIONED and unchanged: "".
    _set_role(monkeypatch, None)
    assert ol._channel_scope_sql() == ""


def test_whitespace_only_role_treated_as_empty(monkeypatch):
    # _body_role() strips + lowercases, so "  " normalizes to the empty role.
    _set_role(monkeypatch, "   ")
    assert ol._channel_scope_sql() == ""


def test_uppercase_role_normalizes(monkeypatch):
    # Case-insensitive: "HUB" is the hub role, not an unknown one.
    _set_role(monkeypatch, "HUB")
    clause = ol._channel_scope_sql()
    assert "channel<>'tmux-console'" in clause


@pytest.mark.parametrize("bad_role", ["hubb", "consoel", "nazim", "cai", "fleet", "x"])
def test_unknown_role_raises_not_empty(monkeypatch, bad_role):
    # THE FIX: an unrecognized non-empty role must FAIL CLOSED (raise), never
    # silently return "" and let a stamp span every channel.
    _set_role(monkeypatch, bad_role)
    with pytest.raises(ValueError) as exc:
        ol._channel_scope_sql()
    # The message names the offending value and the recognized set.
    assert "ORCH_BODY_ROLE" in str(exc.value)


def test_recognized_roles_set_is_exactly_console_hub_empty():
    assert ol._RECOGNIZED_BODY_ROLES == frozenset({"console", "hub", ""})
