"""A lane can be spun UP and never spun DOWN. This is the missing half.

WHY THIS EXISTS. `scripts/lanes.sh` has `ls`, `up` and `attach`. There is no `down`, and nothing
anywhere winds a lane down on idleness — verified 2026-08-15, when the only file in the repo
containing "autoscal" turned out to be CLAUDE.md, in the sentence claiming the manual lane pen was
"interim until the autoscaler subsumes it". A name doing an implementation's job. The substrate is
one-directional: ten irsyad-family lanes exist, most of them idle, each holding context nobody is
reading.

Winding down is NOT a recycle. A recycle clears a body and boots it again on its own handoff; a
wind-down ENDS the session, and whatever was only in that context is gone. So the gates here are
strictly harsher than a recycle's, and every one of them FAILS CLOSED — an unknown answer refuses,
because the cost of a wrong "yes" is lost work and the cost of a wrong "no" is a lane that stays up
another hour.

The gate logic lives here, in Python, rather than inside the shell script, for one reason: it has
to be testable without a live tmux server and a live database. Both the operator-facing
`lanes.sh down` and (later) the SRE's detector call the SAME predicate, so a lane cannot be wound
down by one path under rules the other path would have refused.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.lib import lane_winddown as lw  # noqa: E402


def _probes(busy=False, unread=0, handoff_age=60, composer="empty", exists=True):
    """Every external fact injected, so the predicate is pure and the test is honest."""
    return dict(
        session_exists=lambda s: exists,
        is_busy=lambda s: busy,
        unread_count=lambda s: unread,
        handoff_age_s=lambda s: handoff_age,
        composer_state=lambda s: composer,
    )


def test_an_idle_drained_lane_with_a_fresh_handoff_may_wind_down():
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes())
    assert ok is True, why


def test_a_busy_lane_is_refused():
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(busy=True))
    assert ok is False and "busy" in why.lower()


def test_a_lane_with_unread_work_is_refused():
    """Winding down a lane with queued rows strands the work AND hides it: the row stays
    unread forever because its recipient no longer exists."""
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(unread=2))
    assert ok is False and "unread" in why.lower()


def test_a_lane_without_a_fresh_handoff_is_refused():
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(handoff_age=99999))
    assert ok is False and "handoff" in why.lower()


def test_a_missing_handoff_is_refused_not_treated_as_nothing_to_save():
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(handoff_age=None))
    assert ok is False and "handoff" in why.lower()


def test_real_staged_text_in_the_composer_is_refused():
    """The lane typed a next step for itself and never submitted it. Ending the session
    destroys it — and unlike a reset, there is no wipe here whose result we could read to
    tell a ghost from real text."""
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(composer="real"))
    assert ok is False and "composer" in why.lower()


def test_a_dim_ghost_does_not_block_a_wind_down():
    """5-for-5 on wild ghosts: a dim autosuggestion is not staged work, and letting it veto
    is how lanes end up unreachable AND un-windable."""
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(composer="ghost"))
    assert ok is True, why


def test_an_unreadable_composer_is_refused():
    """Fail closed on unknown: we cannot prove nothing is staged."""
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(composer="unknown"))
    assert ok is False


def test_a_missing_session_is_refused_rather_than_reported_as_success():
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(exists=False))
    assert ok is False and "session" in why.lower()


@pytest.mark.parametrize("body", ["nazim", "cai", "orch", "fleet-health", "fleet-console"])
def test_singleton_bodies_can_never_be_wound_down(body):
    """These are not lanes. Ending cai's session is not elasticity, it is an outage — and the
    check must not depend on the caller remembering which names are singletons."""
    ok, why = lw.may_wind_down(body, **_probes())
    assert ok is False and "singleton" in why.lower()


# --------------------------------------------------------------------------------------
# the operator-facing entrypoint must go through the SAME predicate
# --------------------------------------------------------------------------------------

_LANES_SH = (_ROOT / "scripts" / "lanes.sh").read_text()


def test_lanes_sh_has_a_down_counterpart_to_up():
    """`lanes.sh up` existed with no counterpart, which is why the substrate was
    one-directional and ten irsyad lanes sat parked."""
    assert "down)" in _LANES_SH, "lanes.sh must expose `down` alongside `up`"


def test_lanes_sh_down_delegates_to_the_tested_predicate():
    """If the shell re-implemented the gates, a lane could be wound down by one path under
    rules the other path would have refused — and only one of the two would be tested."""
    assert "lane_winddown" in _LANES_SH


# --------------------------------------------------------------------------------------
# finding the handoff: no single convention exists, so look in all of them and SAY where
# --------------------------------------------------------------------------------------

def test_handoff_is_found_under_the_session_name(tmp_path, monkeypatch):
    monkeypatch.setattr(lw, "_REPO", tmp_path)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "irsyad-prog2-handoff-NOW.md").write_text("x" * 900)
    assert lw.live_handoff_age_s("irsyad-prog2") is not None


def test_handoff_is_found_under_the_lane_handoffs_dir(tmp_path, monkeypatch):
    """Lanes do not share one convention — reports/, reports/lane-handoffs/ and the agent id
    are all in live use. Checking only one of them makes the gate refuse everything, and a
    gate that always refuses is a gate people route around."""
    monkeypatch.setattr(lw, "_REPO", tmp_path)
    (tmp_path / "reports" / "lane-handoffs").mkdir(parents=True)
    (tmp_path / "reports" / "lane-handoffs" / "irsyad-prog2-handoff-NOW.md").write_text("x" * 900)
    assert lw.live_handoff_age_s("irsyad-prog2") is not None


def test_missing_handoff_reports_where_it_looked(tmp_path, monkeypatch):
    """'No handoff' is only actionable if the caller learns which paths were tried."""
    monkeypatch.setattr(lw, "_REPO", tmp_path)
    (tmp_path / "reports").mkdir()
    assert lw.live_handoff_age_s("irsyad-prog2") is None
    assert any("irsyad-prog2" in p for p in lw.handoff_candidates("irsyad-prog2"))


# ---------------------------------------------------------------------------
# The unread gate's P3 floor (orch-console #89707)
# ---------------------------------------------------------------------------

def _captured_unread_sql(monkeypatch, tmp_path):
    """Run live_unread_count against a fake driver and return the SQL it sent.

    A fake DB cannot prove what Postgres does with the predicate; what it CAN prove is
    that the predicate is in the statement the live path sends -- which is the half that
    was missing while the gate gated on an unconditional count.
    """
    import types

    sent = []

    class _Cur:
        def execute(self, sql, params=None):
            sent.append((" ".join(sql.split()), params))

        def fetchone(self):
            return (0,)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.SimpleNamespace(connect=lambda dsn: _Conn())
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    monkeypatch.setattr(lw, "_agent_for_session", lambda s: "cc-hifz-1")
    assert lw.live_unread_count("hifz") == 0
    return sent[0][0]


def test_unread_gate_floors_out_p3(monkeypatch, tmp_path):
    """P3 never wakes a lane anywhere in the fleet, so a P3-only backlog is not work this
    gate can strand. Without the floor a lane was un-windable because of FYI rows nobody
    would ever be woken for (it blocked 4 of 15 lanes during the 2026-11-01 spend pause)."""
    sql = _captured_unread_sql(monkeypatch, tmp_path)
    assert "priority IS DISTINCT FROM 'P3'" in sql


def test_the_floor_excludes_p3_and_only_p3(monkeypatch, tmp_path):
    """An edit that also swallows P2/P1/P0 would silently un-gate real work, so pin the
    predicate to name exactly one priority and to name P3."""
    import re
    sql = _captured_unread_sql(monkeypatch, tmp_path)
    floors = [ln for ln in sql.split(" AND ") if "priority" in ln]
    assert len(floors) == 1, floors
    assert re.findall(r"'(P\d)'", floors[0]) == ["P3"]


def test_the_floor_uses_is_distinct_from_so_null_still_blocks(monkeypatch, tmp_path):
    """A NULL priority must keep counting as work: `<> 'P3'` would drop it and fail toward
    winding down, which is the dangerous direction."""
    sql = _captured_unread_sql(monkeypatch, tmp_path)
    assert "IS DISTINCT FROM 'P3'" in sql
    assert "priority <> 'P3'" not in sql and "priority != 'P3'" not in sql


def test_the_refusal_names_the_priority_range():
    """The message has to say why a lane that clearly has mail is still windable."""
    ok, why = lw.may_wind_down("irsyad-prog2", **_probes(unread=1))
    assert ok is False
    assert "P0–P2" in why and "P3" in why
