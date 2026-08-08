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
    # NEVER the hub (operator-attended) or the operator/console/empty
    assert agent_wake.is_wake_eligible_recipient("cc-orchestrator") is False
    assert agent_wake.is_wake_eligible_recipient("orch-console") is False
    assert agent_wake.is_wake_eligible_recipient("operator") is False
    assert agent_wake.is_wake_eligible_recipient(None) is False


def test_should_auto_wake_behavior_unchanged_by_refactor():
    # the #16838 class: passive update / rr=false / P2 -> NOT realtime-urgent
    assert agent_wake.should_auto_wake("cc-quality", "update", False, "P2", False) is False
    # urgent paths still wake
    assert agent_wake.should_auto_wake("cc-quality", "update", True, "P2", False) is True
    assert agent_wake.should_auto_wake("cc-quality", "review_request", False, "P2", False) is True
    assert agent_wake.should_auto_wake("cc-finance-1", "update", False, "P1", False) is True
    # recipient + test/P3 gates unchanged
    assert agent_wake.should_auto_wake("cc-orchestrator", "blocker", True, "P0", False) is False
    assert agent_wake.should_auto_wake("cc-quality", "blocker", True, "P0", True) is False   # is_test
    assert agent_wake.should_auto_wake("cc-quality", "blocker", True, "P3", False) is False  # P3


# ---- sweep pure logic ----

def _row(to_agent, id=1):
    # SQL SELECT order: id, to_agent, message_type, requires_response, priority, is_test
    return (id, to_agent, "update", False, "P2", False)


def test_sweep_catches_the_16838_class_and_shares_recipient_policy():
    rows = [
        _row("cc-quality", 16838),        # the missed passive update -> MUST be swept
        _row("cc-finance-1", 2),
        _row("cai", 3),
        _row("cc-orchestrator", 4),       # hub -> NEVER swept (shared recipient policy)
        _row("orch-console", 5),          # operator console -> NEVER
    ]
    assert wbs.eligible_recipients(rows) == ["cc-quality", "cc-finance-1", "cai"]


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
