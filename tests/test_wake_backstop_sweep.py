"""wake_backstop_sweep + the shared recipient-policy refactor (op#11297).

Locks cc-quality's corrected spec (#16847): the sweep uses a BROADER trigger than
realtime (any unread/un-skipped/non-test/non-P3 directed row past a grace) while
SHARING the recipient policy (never cc-orchestrator/operator). The #16838 miss
(update / requires_response=false / P2 to an eligible lane) must be caught by the
sweep but is correctly NOT realtime-urgent.
"""
import os
import sys

import psycopg
import pytest

NS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nervous_system")
sys.path.insert(0, NS)

import agent_wake  # noqa: E402
import wake_backstop_sweep as wbs  # noqa: E402

_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


# ---- shared recipient policy (the refactor) ----

def test_is_wake_eligible_recipient_policy():
    assert agent_wake.is_wake_eligible_recipient("cc-quality") is True
    assert agent_wake.is_wake_eligible_recipient("cc-finance-1") is True
    assert agent_wake.is_wake_eligible_recipient("cai") is True
    # wake-A (op#11297): the console is a full-eligibility recipient now.
    assert agent_wake.is_wake_eligible_recipient("orch-console") is True
    # CAI-451/CAI-RESP-786: the hub is eligible ONLY on the narrow floor
    # (P0/P1 AND requires_response). Recipient-only / default context -> not eligible.
    assert agent_wake.is_wake_eligible_recipient("cc-orchestrator") is False
    assert agent_wake.is_wake_eligible_recipient("cc-orchestrator", "P1", True) is True
    assert agent_wake.is_wake_eligible_recipient("cc-orchestrator", "P0", True) is True
    assert agent_wake.is_wake_eligible_recipient("cc-orchestrator", "P1", False) is False
    assert agent_wake.is_wake_eligible_recipient("cc-orchestrator", "P2", True) is False
    # never the operator / empty
    assert agent_wake.is_wake_eligible_recipient("operator") is False
    assert agent_wake.is_wake_eligible_recipient(None) is False


def test_should_auto_wake_behavior_unchanged_by_refactor():
    # the #16838 class: passive update / rr=false / P2 -> NOT realtime-urgent
    assert agent_wake.should_auto_wake("cc-quality", "update", False, "P2", False) is False
    # urgent paths still wake
    assert agent_wake.should_auto_wake("cc-quality", "update", True, "P2", False) is True
    assert agent_wake.should_auto_wake("cc-quality", "review_request", False, "P2", False) is True
    assert agent_wake.should_auto_wake("cc-finance-1", "update", False, "P1", False) is True
    # CAI-451/786: the hub IS woken on the narrow floor (P0/P1 + rr), NOT excluded.
    assert agent_wake.should_auto_wake("cc-orchestrator", "blocker", True, "P0", False) is True
    assert agent_wake.should_auto_wake("cc-orchestrator", "blocker", False, "P0", False) is False  # rr=False
    # recipient + test/P3 gates unchanged
    assert agent_wake.should_auto_wake("cc-quality", "blocker", True, "P0", True) is False   # is_test
    assert agent_wake.should_auto_wake("cc-quality", "blocker", True, "P3", False) is False  # P3


# ---- sweep pure logic ----

def _row(to_agent, id=1, priority="P2", is_test=False):
    # SQL SELECT order: id, to_agent, message_type, requires_response, priority, is_test
    return (id, to_agent, "update", False, priority, is_test)


def test_should_backstop_wake_is_the_wider_predicate():
    # the #16838 class: passive update/rr=false/P2 to an eligible lane -> backstop
    # WAKES it (wider) while realtime should_auto_wake does NOT.
    assert agent_wake.should_backstop_wake("cc-quality", "update", False, "P2", False) is True
    assert agent_wake.should_auto_wake("cc-quality", "update", False, "P2", False) is False
    # still gated: test / P3 / ineligible recipient
    assert agent_wake.should_backstop_wake("cc-quality", "update", False, "P2", True) is False
    assert agent_wake.should_backstop_wake("cc-quality", "update", False, "P3", False) is False
    # CAI-786: the hub is swept ONLY on the narrow floor (P0/P1 + rr) — the belt for
    # exactly the hub-dark class. A P2 / rr=false hub row is still NOT swept.
    assert agent_wake.should_backstop_wake("cc-orchestrator", "blocker", True, "P0", False) is True
    assert agent_wake.should_backstop_wake("cc-orchestrator", "update", False, "P2", False) is False


def test_sweep_catches_the_16838_class_and_shares_recipient_policy():
    rows = [
        _row("cc-quality", 16838),        # the missed passive update -> MUST be swept
        _row("cc-finance-1", 2),
        _row("cai", 3),
        _row("cc-orchestrator", 4),       # hub P2/rr=false -> NOT swept (narrow floor unmet)
        # hub P0+rr -> swept: the CAI-786 belt for the hub-dark class (SQL order tuple)
        (7, "cc-orchestrator", "blocker", True, "P0", False),
        _row("orch-console", 5),          # console (wake-A) -> swept
    ]
    assert wbs.eligible_recipients(rows) == [
        "cc-quality", "cc-finance-1", "cai", "cc-orchestrator", "orch-console"]


def test_eligible_recipients_defense_in_depth_drops_test_and_p3():
    # even if the SQL prefilter let one through, the canonical per-row gate drops it
    rows = [_row("cc-quality", 1), _row("cc-finance-1", 2, is_test=True),
            _row("cai", 3, priority="P3")]
    assert wbs.eligible_recipients(rows) == ["cc-quality"]


def test_sweep_dedups_recipients():
    rows = [_row("cc-quality", 1), _row("cc-quality", 2), _row("cc-finance-1", 3)]
    assert wbs.eligible_recipients(rows) == ["cc-quality", "cc-finance-1"]


def test_sweep_once_wakes_each_target_once_via_injected_wake():
    calls = []
    def fake_wake(agent, reason="", dry_run=False, now=None):
        calls.append(agent)
        return {"woke": True, "session": agent}
    rows = [_row("cc-quality", 1), _row("cc-quality", 2), _row("cc-orchestrator", 3)]
    res = wbs.sweep_once(rows=rows, wake=fake_wake)
    assert calls == ["cc-quality"]                 # once, hub excluded
    assert res["woke"] == ["cc-quality"]
    assert res["considered"] == 3


def test_sweep_quiesces_when_nothing_rotting():
    res = wbs.sweep_once(rows=[], wake=lambda *a, **k: {"woke": True})
    assert res["targets"] == [] and res["woke"] == []


# ---- resolve on the live-session fact (op#11297 #16880 / acceptance #9,#10) ----

def test_first_live_session_picks_first_live_skips_dead():
    live = {"quality"}
    hs = lambda s: s in live
    assert agent_wake._first_live_session(["dead-1", "quality", "dead-2"], has_session=hs) == "quality"
    assert agent_wake._first_live_session(["dead-1", "dead-2"], has_session=hs) is None
    assert agent_wake._first_live_session([], has_session=hs) is None


@pytest.mark.skipif(not _DSN, reason="no DATABASE_URL")
def test_resolve_uses_live_session_not_status_field():
    """A registered agent with a LIVE pane resolves regardless of its status field
    (the #16880 fix: offline-while-alive + {*}-scoped roles). Live fixture: cc-quality
    (offered itself — status=offline, repo_scope={*}, pane 'quality' live). Skips only
    if the fixture is not currently registered/live."""
    with psycopg.connect(_DSN) as c, c.cursor() as cur:
        cur.execute("SELECT status, tmux_session FROM agent_status WHERE agent_id='cc-quality'")
        row = cur.fetchone()
    if not row or not row[1] or not agent_wake._tmux_has_session(row[1]):
        pytest.skip("cc-quality fixture not registered/live")
    status, sess = row
    # the invariant: live pane -> resolves to it, decoupled from the status field
    assert agent_wake.resolve_tmux_session("cc-quality") == sess


# ---- SQL predicate (real DB, BEGIN..ROLLBACK) ----

@pytest.mark.skipif(not _DSN, reason="no DATABASE_URL")
def test_sql_predicate_selects_rotting_and_quiesces_on_read():
    """Insert a passive update/rr=false/P2 aged past grace -> the sweep SQL selects
    it (the #16838 repro); mark it read -> it drops out (drain-quiesce). All inside
    one transaction, rolled back."""
    with psycopg.connect(_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute("""INSERT INTO agent_messages
                   (from_agent,to_agent,message_type,subject,body,requires_response,priority,is_test,created_at)
                   VALUES ('cc-fleet-health','cc-quality','update','sweep-test','x',false,'P2',false, now()-interval '200 seconds')
                   RETURNING id""")
            mid = cur.fetchone()[0]
            cur.execute(wbs._SWEEP_SQL, (wbs.WAKE_SWEEP_GRACE_S,))
            ids = [r[0] for r in cur.fetchall()]
            assert mid in ids, "rotting passive update not selected by sweep SQL"
            # drain -> quiesce
            cur.execute("UPDATE agent_messages SET read_at=now() WHERE id=%s", (mid,))
            cur.execute(wbs._SWEEP_SQL, (wbs.WAKE_SWEEP_GRACE_S,))
            assert mid not in [r[0] for r in cur.fetchall()], "read row still selected (no quiesce)"
        conn.rollback()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
