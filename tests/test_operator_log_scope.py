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


def test_empty_role_is_lane_and_requires_tag(monkeypatch):
    """Fable audit 2026-09-30 (B-2): '' used to be the SANCTIONED unscoped legacy role,
    so a lane (role unset by launch_dangerous_cc.sh) could stamp EVERY channel. Unset
    now means 'lane' and a lane must pass tag= — never an unscoped clause."""
    _set_role(monkeypatch, None)
    assert ol._body_role() == "lane"
    with pytest.raises(ValueError):
        ol._channel_scope_sql()


def test_whitespace_only_role_treated_as_lane(monkeypatch):
    _set_role(monkeypatch, "   ")
    assert ol._body_role() == "lane"
    with pytest.raises(ValueError):
        ol._channel_scope_sql()


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


def test_recognized_roles_set_is_exactly_console_hub_lane():
    assert ol._RECOGNIZED_BODY_ROLES == frozenset({"console", "hub", "lane"})


def test_hub_excludes_finance_console(monkeypatch):
    _set_role(monkeypatch, "hub")
    clause = ol._channel_scope_sql()
    assert "tag IS DISTINCT FROM 'finance-console'" in clause, (
        "the hub must NOT reconcile the finance lane's revenue channel"
    )


def test_console_does_not_claim_finance_console(monkeypatch):
    _set_role(monkeypatch, "console")
    clause = ol._channel_scope_sql()
    # The console's scope is an INCLUSION list; finance-console is absent from it, so a
    # finance-console row is out of console scope (the finance lane answers it, not Nazim).
    assert "finance-console" not in clause


def test_console_scope_includes_cosem_tdu_and_angullia(monkeypatch):
    # op#22517 (2026-09-26/27): the Mini's nazim-ingest already polls these two
    # (boot_nazim_ingest.sh INGEST_CHANNELS) but the reconcile scope never followed,
    # so the hub silently stamped Fazli's cosem-tdu messages "handled" without
    # anyone answering them (12h silence). Console must claim them.
    _set_role(monkeypatch, "console")
    clause = ol._channel_scope_sql()
    assert "cosem-tdu" in clause
    assert "angullia" in clause


def test_hub_excludes_cosem_tdu_and_angullia(monkeypatch):
    _set_role(monkeypatch, "hub")
    clause = ol._channel_scope_sql()
    assert "tag IS DISTINCT FROM 'cosem-tdu'" in clause
    assert "tag IS DISTINCT FROM 'angullia'" in clause


def test_console_scope_includes_oeh(monkeypatch):
    # op#22521 (bus #43713): oeh is console-reconciled the same way as angullia --
    # a client channel with no dedicated coord reconcile loop of its own.
    _set_role(monkeypatch, "console")
    clause = ol._channel_scope_sql()
    assert "oeh" in clause


def test_hub_excludes_oeh(monkeypatch):
    _set_role(monkeypatch, "hub")
    clause = ol._channel_scope_sql()
    assert "tag IS DISTINCT FROM 'oeh'" in clause


def test_console_polled_client_tags_matches_ingest_channels():
    """Guardrail against the op#22517 drift recurring: every client channel the
    Mini's nazim-ingest polls (boot_nazim_ingest.sh INGEST_CHANNELS) that isn't
    the console's own DM tag, a lane-owned tag, or the finance-console carve-out
    must be in _CONSOLE_POLLED_CLIENT_TAGS -- so adding a new channel to
    INGEST_CHANNELS without updating the reconcile scope fails CI immediately,
    instead of silently dropping client messages for 12h like Fazli's."""
    import re
    from pathlib import Path
    script = Path(__file__).resolve().parent.parent / "scripts" / "boot_nazim_ingest.sh"
    text = script.read_text()
    m = re.search(r'INGEST_CHANNELS="([^"]+)"', text)
    assert m, "boot_nazim_ingest.sh no longer sets INGEST_CHANNELS -- update this test"
    ingest_tags = frozenset(t.strip() for t in m.group(1).split(",") if t.strip())
    expected = ingest_tags.difference({"nazim-console", "finance-console"}, ol._LANE_OWNED_TAGS)
    assert expected == ol._CONSOLE_POLLED_CLIENT_TAGS, (
        f"boot_nazim_ingest.sh polls {sorted(ingest_tags)} but "
        f"_CONSOLE_POLLED_CLIENT_TAGS is {sorted(ol._CONSOLE_POLLED_CLIENT_TAGS)} -- "
        "a channel was added to one and not the other (op#22517 drift)."
    )
