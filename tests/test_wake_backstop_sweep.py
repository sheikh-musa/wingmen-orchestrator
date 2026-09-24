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
    def fake_wake(agent, reason="", dry_run=False, now=None, row_id=None):
        calls.append(agent)
        return {"woke": True, "session": agent}
    rows = [_row("cc-quality", 1), _row("cc-quality", 2), _row("cc-orchestrator", 3)]
    res = wbs.sweep_once(rows=rows, wake=fake_wake)
    assert calls == ["cc-quality"]                 # once, hub excluded
    assert res["woke"] == ["cc-quality"]
    assert res["considered"] == 3


def test_sweep_passes_stable_representative_row_id_per_agent():
    # Per-row ceiling (Nazim #43063): the wake must be keyed to a STABLE row — the agent's
    # oldest (min id) unread row — so successive sweeps of the SAME stale row hit the ceiling.
    seen = {}
    def fake_wake(agent, reason="", dry_run=False, now=None, row_id=None):
        seen[agent] = row_id
        return {"woke": True, "session": agent}
    rows = [_row("cc-quality", 7), _row("cc-quality", 2), _row("cc-quality", 5)]
    wbs.sweep_once(rows=rows, wake=fake_wake)
    assert seen["cc-quality"] == 2, "must key to the oldest (min id) fresh row, stably"


def test_sweep_quiesces_when_nothing_rotting():
    res = wbs.sweep_once(rows=[], wake=lambda *a, **k: {"woke": True})
    assert res["targets"] == [] and res["woke"] == []


# ---- BACKOFF: dead-target reachability skip (A) + per-row age cap (B) [Nazim 37512] ----
from datetime import datetime, timedelta, timezone  # noqa: E402

_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def _row_ts(to_agent, id=1, age_s=0, priority="P2", is_test=False, rr=False, mt="update"):
    """7-tuple incl created_at (age_s seconds before _NOW), for the age cap."""
    ca = _NOW - timedelta(seconds=age_s)
    return (id, to_agent, mt, rr, priority, is_test, ca)


def _collector():
    marked, pages = [], []

    def mark(ids):
        marked.extend(ids)
        return list(ids)          # mark returns the ids it set (CAS contract)

    return marked, pages, mark, (lambda s, b: pages.append((s, b)))


def test_fresh_row_to_unreachable_agent_is_not_quiesced_waits_for_cap():
    # Nazim #42994 amend B: a FRESH (under-cap) row to an agent with no local live pane is NOT
    # quiesced — quiesce only ever happens past cap. The fresh row drives a wake (which comes
    # back 'no live session' -> observability in `unreachable`) and is left unread.
    marked, pages, mark, page = _collector()
    rows = [_row_ts("cc-cosem-platform", 101), _row_ts("cc-cosem-platform", 102)]  # age_s=0 (fresh)
    res = wbs.sweep_once(rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
                         now_dt=_NOW, cap_age_s=390, mark=mark, escalate=page)
    assert marked == [] and pages == []                # fresh -> never quiesced
    assert res["unreachable"] == ["cc-cosem-platform"]
    assert res["dead_foreign"] == []


def test_capped_row_to_lease_fresh_hub_is_live_stuck_not_dead_the_451e110_class():
    # The cross-host hub (cc-orchestrator) has NO local pane and NO heartbeat — its liveness is
    # the orch_lease. A capped row to a LEASE-FRESH hub is ALIVE, so it is handled as live-stuck
    # (B), NEVER dead-quiesced (the 451e110 false-DEAD class). matching_hbs=[] but lease fresh.
    marked, pages, mark, page = _cas_collector()
    rows = [_row_ts("cc-orchestrator", 111, age_s=_PAST_CAP, priority="P0", rr=True),
            _row_ts("cc-orchestrator", 112, age_s=_PAST_CAP, priority="P0", rr=True)]
    res = wbs.sweep_once(rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
                         now_dt=_NOW, cap_age_s=390, mark=mark, escalate=page,
                         matching_hbs=lambda a: [], desired_state_of=lambda a: None,
                         base_of=lambda a: None, hub_lease_fresh=lambda: True, escalated_seen=set())
    assert res["live_stuck"] == ["cc-orchestrator"] and res["dead_foreign"] == []
    # (B) split (Nazim #43063): alive → QUIESCE only, NO page at cap (page deferred to stuck-pass)
    assert set(marked) == {111, 112} and pages == []


def test_live_fresh_row_still_woken_and_not_escalated():
    # unchanged path: fresh under-cap row to a reachable agent -> woke, nothing marked/escalated
    marked, pages, mark, page = _collector()
    res = wbs.sweep_once(rows=[_row_ts("cc-quality", 301, age_s=100)],
                         wake=lambda a, **k: {"woke": True}, now_dt=_NOW, cap_age_s=390,
                         mark=mark, escalate=page)
    assert res["woke"] == ["cc-quality"] and marked == [] and pages == []
    assert res["unreachable"] == []


def test_capped_dead_foreign_row_never_drives_a_wake_and_is_marked_once():
    # a past-cap row to a gone agent: capped rows never drive a wake; the dead-foreign path
    # marks it exactly once (deps injected so it is hermetic — no DB).
    marked, pages, mark, page = _collector()
    woke_calls = []
    res = wbs.sweep_once(rows=[_row_ts("cc-cosem-platform", 501, age_s=10_000)],
                         wake=lambda a, **k: (woke_calls.append(a), {"woke": False, "why": "no live session"})[1],
                         now_dt=_NOW, cap_age_s=390, mark=mark, escalate=page,
                         matching_hbs=lambda a: [], desired_state_of=lambda a: "down",
                         base_of=lambda a: None, escalated_seen=set())
    assert woke_calls == []                      # capped rows are pulled out before waking
    assert marked.count(501) == 1               # marked once, not twice
    assert res["unreachable"] == []             # never reached the wake/reachability path
    assert res["dead_foreign"] == ["cc-cosem-platform"]


def test_dry_run_mutates_nothing():
    marked, pages, mark, page = _collector()
    rows = [_row_ts("cc-cosem-platform", 601), _row_ts("cai", 602, age_s=10_000)]
    res = wbs.sweep_once(rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
                         now_dt=_NOW, cap_age_s=390, mark=mark, escalate=page, dry_run=True,
                         matching_hbs=lambda a: [], desired_state_of=lambda a: "down",
                         base_of=lambda a: None, escalated_seen=set())
    assert marked == [] and pages == []         # observe-only: no quiesce, no escalation
    # 601 (cc-cosem-platform, age 0) is FRESH -> wake path; 602 (cai) is capped+gone -> would-quiesce
    assert res["dead_foreign"] == ["cai"]


def test_legacy_6tuple_rows_never_capped():
    # a 6-tuple (no created_at) must not be aged/capped — fail toward keeping the backstop
    marked, pages, mark, page = _collector()
    res = wbs.sweep_once(rows=[_row("cc-quality", 701)], wake=lambda a, **k: {"woke": True},
                         now_dt=_NOW, cap_age_s=1, mark=mark, escalate=page)
    assert res["capped"] == [] and res["woke"] == ["cc-quality"] and marked == []


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


# ---- DEAD-FOREIGN quiesce, substrate-liveness based (Nazim #42994 (2) + 4 amendments) ----
# Replaces the owns()-scoped (A)/(B) give-up with a cross-host substrate-liveness rule:
# quiesce a rotting row's agent ONLY when the row is past CAP_AGE AND the agent is gone on
# EVERY host (no matching agent_status row — base-inclusive — with a heartbeat fresher than
# GONE_WINDOW_S ~2h) AND desired_state is not 'up' (veto, fail-closed on lookup error).
# CAS on skipped_at IS NULL is the once-guard: escalate only when the mark actually set rows.

def _hb(age_s):
    """A last_heartbeat that is age_s seconds before _NOW."""
    return _NOW - timedelta(seconds=age_s)


def _cas_collector():
    """mark simulates the CAS `UPDATE ... WHERE skipped_at IS NULL RETURNING id`: it marks
    only ids not already marked and RETURNS the newly-marked ids (so a 2nd sweep marks/returns
    nothing). escalate records (subject, body)."""
    marked, pages = [], []

    def mark(ids):
        new = [i for i in (ids or []) if i not in marked]
        marked.extend(new)
        return new

    return marked, pages, mark, (lambda s, b: pages.append((s, b)))


_PAST_CAP = 10_000   # well past cap_age_s=390


def test_base_addressed_row_to_live_instance_is_not_quiesced_amendA():
    # (A) agent_status is keyed by INSTANCE id (cc-substrate-1) with base in base_agent_id;
    # bus rows address the BASE (cc-substrate). A base-inclusive match must find the live
    # instance's fresh heartbeat and NOT quiesce. Without the base match this live lane would
    # be called dead and its directed row silently dropped.
    marked, pages, mark, page = _cas_collector()
    rows = [_row_ts("cc-substrate", 101, age_s=_PAST_CAP)]
    res = wbs.sweep_once(
        rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
        now_dt=_NOW, cap_age_s=390,
        matching_hbs=lambda a: [_hb(60)] if a == "cc-substrate" else [],   # cc-substrate-1 fresh
        desired_state_of=lambda a: "up", base_of=lambda a: None,
        mark=mark, escalate=page, escalated_seen=set())
    # base-inclusive match finds cc-substrate-1's fresh hb -> ALIVE, so it is handled as
    # live-but-stuck (B), NEVER as a dead agent. That is the amend-A protection.
    assert "cc-substrate" not in res["dead_foreign"]
    assert res["live_stuck"] == ["cc-substrate"] and marked == [101]


def test_stale_heartbeat_under_gone_window_is_not_dead_amendB():
    # (B) a stale heartbeat is not death — the hb daemon can die while the pane lives. A matching
    # row with a heartbeat 1h old (< the 2h gone-window) means NOT gone -> handled as live-stuck,
    # never as a dead agent.
    marked, pages, mark, page = _cas_collector()
    rows = [_row_ts("cc-someworker", 111, age_s=_PAST_CAP)]
    res = wbs.sweep_once(
        rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
        now_dt=_NOW, cap_age_s=390, gone_window_s=7200,
        matching_hbs=lambda a: [_hb(3600)],   # 1h old -> stale but < 2h gone-window
        desired_state_of=lambda a: "down", base_of=lambda a: None,
        mark=mark, escalate=page, escalated_seen=set())
    assert "cc-someworker" not in res["dead_foreign"]
    assert res["live_stuck"] == ["cc-someworker"]   # stale<2h = alive-but-stuck (B), not dead


def test_desired_state_up_vetoes_quiesce_amendC():
    # (C) veto: never quiesce a lane the operator WANTS up, even if gone on every host — a
    # wanted-up-but-dark lane is a page/flag (singleton/lane watchdog), not a delivery-drop.
    marked, pages, mark, page = _cas_collector()
    rows = [_row_ts("cc-wantedup", 121, age_s=_PAST_CAP)]
    res = wbs.sweep_once(
        rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
        now_dt=_NOW, cap_age_s=390,
        matching_hbs=lambda a: [],             # gone everywhere
        desired_state_of=lambda a: "up", base_of=lambda a: None,  # operator wants it up -> VETO
        mark=mark, escalate=page, escalated_seen=set())
    assert marked == [] and pages == []        # vetoed -> NOT quiesced, not escalated
    assert res["vetoed"] == ["cc-wantedup"] and "cc-wantedup" not in res["dead_foreign"]


def test_desired_state_lookup_error_fails_closed_escalates_not_quiesced_amendC():
    # (C) fail-closed: if the desired_state lookup errors (DB error / ambiguous match) we can't
    # prove it's safe to quiesce -> DO NOT quiesce; escalate for a human instead.
    marked, pages, mark, page = _cas_collector()
    def boom(a): raise RuntimeError("ambiguous fleet_lanes match")
    rows = [_row_ts("cc-ambiguous", 131, age_s=_PAST_CAP)]
    res = wbs.sweep_once(
        rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
        now_dt=_NOW, cap_age_s=390,
        matching_hbs=lambda a: [],             # gone everywhere
        desired_state_of=boom, base_of=lambda a: None,
        mark=mark, escalate=page, escalated_seen=set())
    assert marked == []                        # fail-closed: never quiesce on an unproven check
    assert len(pages) == 1                     # but DO escalate for a human
    assert "cc-ambiguous" in res["lookup_failed"]


def test_dead_foreign_quiesced_once_escalated_once_second_sweep_noop_amendD():
    # (5) the whole point: a row past cap to an agent gone on EVERY host (no matching hb) and
    # not wanted-up -> quiesce (CAS) + ONE escalation naming the agent, ids and evidence; a 2nd
    # sweep over the same (now-marked) rows does nothing (skipped_at is the once-guard).
    marked, pages, mark, page = _cas_collector()
    rows = [_row_ts("cc-cosem-platform", 501, age_s=_PAST_CAP),
            _row_ts("cc-cosem-platform", 502, age_s=_PAST_CAP)]
    kw = dict(wake=lambda a, **k: {"woke": False, "why": "no live session"},
              now_dt=_NOW, cap_age_s=390, gone_window_s=7200,
              matching_hbs=lambda a: [],           # gone on every host
              desired_state_of=lambda a: "down",   # not wanted-up
              base_of=lambda a: None, mark=mark, escalate=page, escalated_seen=set())
    res1 = wbs.sweep_once(rows=rows, **kw)
    assert set(marked) == {501, 502}               # both rows quiesced
    assert res1["dead_foreign"] == ["cc-cosem-platform"]
    assert len(pages) == 1                          # ONE page for the dead agent, not per-row
    subj, body = pages[0]
    assert "cc-cosem-platform" in subj
    assert "501" in body and "502" in body          # names the row ids (evidence)
    assert "skipped_at" in body                      # tells the human clearing it re-delivers
    # 2nd sweep over the SAME rows: CAS mark returns nothing already-marked -> no-op
    res2 = wbs.sweep_once(rows=rows, **kw)
    assert set(marked) == {501, 502}               # unchanged
    assert len(pages) == 1                          # NOT re-escalated


def test_dead_foreign_matches_base_exactly_not_by_prefix():
    # a sibling cosem lane (cc-cosem-adcda-1) being alive must NOT keep cc-cosem-platform alive:
    # the match is exact (agent_id = X OR base_agent_id = X), never a 'cc-cosem%' prefix.
    marked, pages, mark, page = _cas_collector()
    def hbs(a):
        # only cc-cosem-adcda has a live instance; cc-cosem-platform has none
        return [_hb(60)] if a in ("cc-cosem-adcda", "cc-cosem-platform-never") else []
    rows = [_row_ts("cc-cosem-platform", 601, age_s=_PAST_CAP)]
    res = wbs.sweep_once(
        rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
        now_dt=_NOW, cap_age_s=390, matching_hbs=hbs, base_of=lambda a: None,
        desired_state_of=lambda a: "down", mark=mark, escalate=page, escalated_seen=set())
    assert marked == [601]                          # still dead — sibling did not spare it
    assert res["dead_foreign"] == ["cc-cosem-platform"]


# ---- PR#139 review fixes (Nazim bus 43007): B-restore, human-filter, instance→base, guard ----

def test_capped_row_to_ALIVE_agent_is_quiesced_but_NOT_paged_at_cap_amend_43063():
    # (B) SPLIT (Nazim #43063): a row past cap to a LIVE agent is QUIESCED (stop re-poking) but NOT
    # paged at cap_age — a ~7min-unread row is normal latency, not stuck. No stuck_rows injected =
    # nothing past stuck_page_age yet → zero pages. This is the fix for the false-page storm.
    marked, pages, mark, page = _cas_collector()
    rows = [_row_ts("cc-quality", 201, age_s=_PAST_CAP)]
    kw = dict(wake=lambda a, **k: {"woke": False, "why": "no live session"},
              now_dt=_NOW, cap_age_s=390, matching_hbs=lambda a: [_hb(60)],  # alive
              desired_state_of=lambda a: "up", mark=mark, escalate=page, escalated_seen=set())
    r1 = wbs.sweep_once(rows=rows, **kw)
    assert marked == [201]                          # quiesced (re-poking stopped)
    assert pages == []                              # but NOT paged at cap (the fix)
    assert r1["live_stuck"] == ["cc-quality"] and "cc-quality" not in r1["dead_foreign"]


def test_capped_row_to_human_operator_is_never_classified_dead():
    # Problem 2: `capped` classification must be filtered by should_backstop_wake, so a human /
    # operator recipient (musa, cto-desktop — ineligible, no lane, no hb) is NEVER quiesced or
    # paged as a "dead agent". They simply are not the backstop's business.
    marked, pages, mark, page = _cas_collector()
    rows = [_row_ts("musa", 301, age_s=_PAST_CAP)]
    r = wbs.sweep_once(rows=rows, wake=lambda a, **k: {"woke": False, "why": "no live session"},
                       now_dt=_NOW, cap_age_s=390, matching_hbs=lambda a: [],
                       desired_state_of=lambda a: None, mark=mark, escalate=page, escalated_seen=set())
    assert marked == [] and pages == []
    assert r["dead_foreign"] == [] and "musa" not in r.get("live_stuck", [])


def test_dead_instance_of_a_live_base_is_readdressed_not_quiesced():
    # Problem 3 (finish amend A): a row to a DEAD instance (cc-irsyad-2, no rows) whose BASE
    # (cc-irsyad) has a live instance must NOT be silently quiesced as dead — escalate ONCE
    # "re-address to <base>" and leave it deliverable. Once-guarded (no skipped_at to lean on).
    marked, pages, mark, page = _cas_collector()
    hbs = lambda a: [_hb(60)] if a == "cc-irsyad" else []      # base alive, instance-2 gone
    rows = [_row_ts("cc-irsyad-2", 401, age_s=_PAST_CAP)]
    seen = set()
    kw = dict(wake=lambda a, **k: {"woke": False, "why": "no live session"},
              now_dt=_NOW, cap_age_s=390, matching_hbs=hbs,
              base_of=lambda a: "cc-irsyad" if a == "cc-irsyad-2" else None,
              desired_state_of=lambda a: "down", mark=mark, escalate=page, escalated_seen=seen)
    r1 = wbs.sweep_once(rows=rows, **kw)
    assert marked == []                             # NOT quiesced (base is alive)
    assert len(pages) == 1 and "cc-irsyad" in pages[0][1]  # re-address to base
    assert r1["dead_foreign"] == [] and r1["readdress"] == ["cc-irsyad-2"]
    r2 = wbs.sweep_once(rows=rows, **kw)
    assert len(pages) == 1                          # once-guarded, not every sweep


def test_lookup_failure_escalates_once_per_process_not_every_sweep():
    # Problem 4: the fail-closed lookup-error escalation must NOT page every 60s sweep. A
    # process-level seen-set guards it to the first failure per agent.
    marked, pages, mark, page = _cas_collector()
    def boom(a): raise RuntimeError("ambiguous fleet_lanes match")
    rows = [_row_ts("cc-ambiguous", 131, age_s=_PAST_CAP)]
    seen = set()
    kw = dict(wake=lambda a, **k: {"woke": False, "why": "no live session"},
              now_dt=_NOW, cap_age_s=390, matching_hbs=lambda a: [],
              desired_state_of=boom, mark=mark, escalate=page, escalated_seen=seen)
    wbs.sweep_once(rows=rows, **kw)
    wbs.sweep_once(rows=rows, **kw)
    assert marked == []                             # fail-closed: never quiesced
    assert len(pages) == 1                          # escalated ONCE, not per-sweep


# ---- (B) STUCK-PAGE split (Nazim #43063): page only at STUCK_PAGE_AGE, not at cap ----

def test_stuck_row_to_alive_agent_paged_once_with_pane_state_43063():
    # A QUIESCED-but-still-unread row past stuck_page_age to a LIVE agent → escalate ONCE, with the
    # lane's pane state in the text; the stuck-page does NOT quiesce (already quiesced). 2nd sweep
    # over the same rows (same `seen`) does NOT re-page — the once-guard holds across sweeps.
    marked, pages, mark, page = _cas_collector()
    seen = set()
    stuck = [_row_ts("cc-quality", 201, age_s=2000)]   # past default stuck_page_age (1800)
    kw = dict(rows=[], stuck_rows=stuck, wake=lambda a, **k: {"woke": False},
              now_dt=_NOW, matching_hbs=lambda a: [_hb(60)],   # alive
              pane_state=lambda a: "busy", mark=mark, escalate=page, escalated_seen=seen)
    r1 = wbs.sweep_once(**kw)
    assert marked == []                                # stuck-page never quiesces (already quiesced)
    assert len(pages) == 1 and "busy" in pages[0][0]   # pane state surfaced in the page
    assert "201" in pages[0][1] and r1["stuck_paged"] == ["cc-quality"]
    r2 = wbs.sweep_once(**kw)
    assert len(pages) == 1                             # once-guarded: no re-page on the 2nd sweep


def test_stuck_row_to_dead_agent_not_paged_already_dead_foreign():
    # a quiesced-unread row to a GONE agent must NOT stuck-page — its rows were already
    # dead-foreign-escalated; only LIVE agents get the stuck page.
    marked, pages, mark, page = _cas_collector()
    stuck = [_row_ts("cc-cosem-platform", 501, age_s=2000)]
    r = wbs.sweep_once(rows=[], stuck_rows=stuck, wake=lambda a, **k: {"woke": False},
                       now_dt=_NOW, matching_hbs=lambda a: [], base_of=lambda a: None,
                       pane_state=lambda a: "no-live-pane", mark=mark, escalate=page, escalated_seen=set())
    assert pages == [] and r["stuck_paged"] == []


def test_stuck_page_ignores_ineligible_recipients():
    # the stuck-page pass shares should_backstop_wake: a human/operator (musa) is never stuck-paged.
    marked, pages, mark, page = _cas_collector()
    stuck = [_row_ts("musa", 301, age_s=2000)]
    r = wbs.sweep_once(rows=[], stuck_rows=stuck, wake=lambda a, **k: {"woke": False},
                       now_dt=_NOW, matching_hbs=lambda a: [], pane_state=lambda a: "idle",
                       mark=mark, escalate=page, escalated_seen=set())
    assert pages == [] and r["stuck_paged"] == []


def test_stuck_page_upper_age_bound_row_older_than_max_never_paged_43073():
    # Nazim #43073: the in-memory once-guard resets on restart, so an UPPER bound stops a restart
    # re-paging very old rows. A row 25h old (past the 24h default max) is NEVER stuck-paged.
    marked, pages, mark, page = _cas_collector()
    old = [_row_ts("cc-quality", 201, age_s=25 * 3600)]   # 25h > 24h max
    r = wbs.sweep_once(rows=[], stuck_rows=old, wake=lambda a, **k: {"woke": False},
                       now_dt=_NOW, matching_hbs=lambda a: [_hb(60)],  # alive (would page but for age)
                       pane_state=lambda a: "busy", mark=mark, escalate=page, escalated_seen=set())
    assert pages == [] and r["stuck_paged"] == []
    # sanity: the SAME row 2000s old (inside the window) WOULD page — proving age is the reason
    marked2, pages2, mark2, page2 = _cas_collector()
    inwin = [_row_ts("cc-quality", 201, age_s=2000)]
    r2 = wbs.sweep_once(rows=[], stuck_rows=inwin, wake=lambda a, **k: {"woke": False},
                        now_dt=_NOW, matching_hbs=lambda a: [_hb(60)],
                        pane_state=lambda a: "busy", mark=mark2, escalate=page2, escalated_seen=set())
    assert len(pages2) == 1 and r2["stuck_paged"] == ["cc-quality"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
