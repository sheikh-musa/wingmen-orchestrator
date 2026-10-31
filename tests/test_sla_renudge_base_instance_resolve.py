"""renudge() must resolve an INSTANCE-addressed agent to its BASE lane.

fleet_lanes keys by base_agent_id (cc-substrate -> substrate-cleanup), but a
violation row can be addressed to a live numbered instance (cc-substrate-1).
An exact-only lookup missed, fell through to ("unreachable", False), and every
re-nudge of an instance lane silently became a console escalation instead of a
lane nudge — re-creating the bottleneck the SRE exists to remove.

Exact match must still win (no regression for base-addressed rows).
"""
from scripts import priority_sla_watchdog as s


LANES = {"cc-substrate": "substrate-cleanup", "cc-coffeemedia": "coffeemedia"}


def test_instance_agent_resolves_to_its_base_lane():
    mech, ok = s.renudge("cc-substrate-1", 2, LANES, dry=True)
    assert ok is True
    assert mech == "lane_nudge.sh:substrate-cleanup"


def test_exact_base_agent_still_resolves_directly():
    # No regression: a row addressed to the base id behaves exactly as before.
    mech, ok = s.renudge("cc-substrate", 1, LANES, dry=True)
    assert ok is True
    assert mech == "lane_nudge.sh:substrate-cleanup"


def test_exact_key_wins_over_base_strip():
    # If BOTH the instance and the base are real lanes, the exact entry wins.
    lanes = {"cc-coffeemedia": "coffeemedia", "cc-coffeemedia-1": "coffeemedia-new"}
    mech, ok = s.renudge("cc-coffeemedia-1", 1, lanes, dry=True)
    assert ok is True
    assert mech == "lane_nudge.sh:coffeemedia-new"


def test_unknown_agent_is_still_unreachable():
    # Fail-loud preserved: no exact key and no base key -> unreachable, never a guess.
    mech, ok = s.renudge("cto-desktop", 1, LANES, dry=True)
    assert ok is False
    assert mech == "unreachable"


def test_base_agent_id_strips_only_a_trailing_instance_number():
    assert s._base_agent_id("cc-substrate-1") == "cc-substrate"
    assert s._base_agent_id("cc-irsyad-6") == "cc-irsyad"
    # A base with no instance suffix is returned unchanged...
    assert s._base_agent_id("musa") == "musa"
    # ...and digits INSIDE the name are never stripped.
    assert s._base_agent_id("cc-substrate") == "cc-substrate"


# ---------------------------------------------------------------------------
# The INSTANCE's own recorded session beats the base lane.
#
# lane_map() collapses a multi-lane base to ONE arbitrary session, so for a base
# like cc-cosem-platform (six lanes live on 2026-10-31) resolving -2 to "the base
# lane" is a coin flip: it can wake a body that was never addressed while the
# addressed one stays asleep. agent_status.tmux_session is the precise answer.
# ---------------------------------------------------------------------------


class _FakeCur:
    def __init__(self, row):
        self._row = row
        self.executed = None

    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, row):
        self._row = row
        self.cur = _FakeCur(row)

    def cursor(self):
        return self.cur


def test_instance_row_session_beats_the_base_lane():
    LANES_MULTI = {"cc-cosem-platform": "cosem-platform-author"}  # arbitrary sibling
    mech, ok = s.renudge("cc-cosem-platform-2", 3, LANES_MULTI, dry=True,
                         conn=_FakeConn(("cosem-platform-calendar",)))
    assert ok is True
    assert mech == "lane_nudge.sh:cosem-platform-calendar"


def test_no_live_instance_row_falls_back_to_the_base_lane():
    # Row absent / stale / not an instance -> None -> exactly the old behaviour.
    mech, ok = s.renudge("cc-cosem-platform-2", 3, {"cc-cosem-platform": "sib"}, dry=True,
                         conn=_FakeConn(None))
    assert ok is True
    assert mech == "lane_nudge.sh:sib"


def test_exact_lane_entry_still_wins_over_the_instance_lookup():
    # An exact fleet_lanes key is authoritative; the DB is never consulted for it.
    conn = _FakeConn(("should-not-be-used",))
    mech, ok = s.renudge("cosem-foo", 1, {"cosem-foo": "lane-exact"}, dry=True, conn=conn)
    assert mech == "lane_nudge.sh:lane-exact"
    assert conn.cur.executed is None


def test_instance_lookup_without_a_conn_is_none():
    assert s._instance_tmux_session(None, "cc-substrate-1") is None
    assert s._instance_tmux_session(_FakeConn(("sess",)), "") is None


def test_instance_lookup_swallows_db_errors_into_the_old_fallback():
    class _Boom:
        def cursor(self):
            raise RuntimeError("db down")
    assert s._instance_tmux_session(_Boom(), "cc-substrate-1") is None


def test_instance_lookup_filters_on_liveness_in_the_query():
    # The guard lives in the SQL, so assert it is actually there: a stale row must
    # never hand back a session name that may now belong to a different lane.
    conn = _FakeConn(("sess",))
    s._instance_tmux_session(conn, "cc-substrate-1")
    sql, params = conn.cur.executed
    flat = " ".join(sql.split()).lower()
    assert "status = 'working'" in flat
    assert "last_heartbeat > now()" in flat
    assert "agent_id <> base_agent_id" in flat
    assert "tmux_session is not null" in flat
    assert params == ("cc-substrate-1", s.INSTANCE_SESSION_MAX_HB_MIN)
