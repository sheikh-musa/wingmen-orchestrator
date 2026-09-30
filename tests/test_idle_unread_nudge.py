"""Idle-while-unread nudge (Fable audit follow-up, orch-console bus #46860 item 2).

cc-oeh sat "standing by for cc-quality's verdict" for ~1h while that verdict (#46766) was
unread in ITS OWN inbox. wake_backstop_sweep's cap/quiesce machinery is about DELIVERY
ATTEMPTS (op#11297) — a distinct, simpler signal is missing: "this pane is doing nothing
right now while a directed row sits unread" (a lane can glance at its inbox and go idle
again without stamping read_at — wake_backstop_sweep's own comments name this class:
"many lanes read their inbox without stamping read_at").

idle_unread_sweep() runs every tick of the SAME daemon loop as sweep_once()
(nervous_system/wake_backstop_sweep.py, wired into main()) — "near the wake-backstop
sweep" — reusing the ONE pane-busy definition (agent_wake._pane_busy) and the ONE
recipient-eligibility definition (agent_wake.is_wake_eligible_recipient). Delivery is via
the NORMAL doorbell (agent_wake.wake_agent) — never a raw tmux send-keys.

Pure-logic tests: no DB, no tmux — every dependency (session resolution, pane-busy check,
nudge/warn senders, the age clock) is injected.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

NS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nervous_system")
sys.path.insert(0, NS)

import wake_backstop_sweep as wbs  # noqa: E402

_NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def _row(rid, agent, age_s):
    """(id, to_agent, created_at) — age_s seconds before _NOW."""
    return (rid, agent, _NOW - timedelta(seconds=age_s))


def _collector():
    nudged, warned = [], []

    def nudge(agent, ids):
        nudged.append((agent, tuple(ids)))

    def warn(agent, ids):
        warned.append((agent, tuple(ids)))

    return nudged, warned, nudge, warn


def _run(rows, *, busy=False, session="sess", nudge=None, warn=None,
        nudged_seen=None, warned_seen=None, now_dt=_NOW):
    if nudge is None or warn is None:
        _n, _w, nudge, warn = _collector()
    return wbs.idle_unread_sweep(
        rows=rows, resolve_session=lambda a: session, pane_busy=lambda s: busy,
        nudge=nudge, warn=warn, now_dt=now_dt,
        nudged_seen={} if nudged_seen is None else nudged_seen,
        warned_seen={} if warned_seen is None else warned_seen,
    ), nudge, warn


# ---- fires ----

def test_fires_for_idle_pane_with_unread_row_past_15min():
    nudged, warned, nudge, warn = _collector()
    res = wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 1000)],  # ~16.7 min
                                resolve_session=lambda a: "sess", pane_busy=lambda s: False,
                                nudge=nudge, warn=warn, now_dt=_NOW, nudged_seen={}, warned_seen={})
    assert nudged == [("cc-oeh", (1,))]
    assert res["nudged"] == ["cc-oeh"]
    assert warned == []


def test_nudge_names_every_unread_id_for_the_agent():
    nudged, warned, nudge, warn = _collector()
    wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 1000), _row(2, "cc-oeh", 950)],
                          resolve_session=lambda a: "sess", pane_busy=lambda s: False,
                          nudge=nudge, warn=warn, now_dt=_NOW, nudged_seen={}, warned_seen={})
    assert nudged == [("cc-oeh", (1, 2))]


# ---- dedupes ----

def test_dedupes_the_same_situation_across_sweeps():
    nudged, warned, nudge, warn = _collector()
    seen = {}
    kw = dict(rows=[_row(1, "cc-oeh", 1000)], resolve_session=lambda a: "sess",
              pane_busy=lambda s: False, nudge=nudge, warn=warn, now_dt=_NOW,
              nudged_seen=seen, warned_seen={})
    wbs.idle_unread_sweep(**kw)
    wbs.idle_unread_sweep(**kw)   # identical id-set -> no second nudge
    assert len(nudged) == 1


def test_renudges_when_a_new_unread_row_appears_for_the_same_agent():
    nudged, warned, nudge, warn = _collector()
    seen = {}
    wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 1000)], resolve_session=lambda a: "sess",
                          pane_busy=lambda s: False, nudge=nudge, warn=warn, now_dt=_NOW,
                          nudged_seen=seen, warned_seen={})
    wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 1000), _row(2, "cc-oeh", 950)],
                          resolve_session=lambda a: "sess", pane_busy=lambda s: False,
                          nudge=nudge, warn=warn, now_dt=_NOW, nudged_seen=seen, warned_seen={})
    assert len(nudged) == 2 and nudged[1] == ("cc-oeh", (1, 2))


# ---- skips a busy pane ----

def test_skips_a_busy_pane():
    nudged, warned, nudge, warn = _collector()
    res = wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 1000)], resolve_session=lambda a: "sess",
                                pane_busy=lambda s: True, nudge=nudge, warn=warn, now_dt=_NOW,
                                nudged_seen={}, warned_seen={})
    assert nudged == [] and res["skipped_busy"] == ["cc-oeh"]


# ---- skips rows < 15 min ----

def test_skips_rows_under_15_minutes():
    nudged, warned, nudge, warn = _collector()
    res = wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 600)], resolve_session=lambda a: "sess",  # 10 min
                                pane_busy=lambda s: False, nudge=nudge, warn=warn, now_dt=_NOW,
                                nudged_seen={}, warned_seen={})
    assert nudged == [] and res["nudged"] == []


def test_mixed_ages_only_the_15min_plus_row_is_named():
    nudged, warned, nudge, warn = _collector()
    wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 600), _row(2, "cc-oeh", 1000)],
                          resolve_session=lambda a: "sess", pane_busy=lambda s: False,
                          nudge=nudge, warn=warn, now_dt=_NOW, nudged_seen={}, warned_seen={})
    assert nudged == [("cc-oeh", (2,))]      # id 1 (10 min) not named


# ---- no live session (a different watchdog's job) ----

def test_skips_a_dead_agent_no_live_session():
    nudged, warned, nudge, warn = _collector()
    res = wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 1000)], resolve_session=lambda a: None,
                                pane_busy=lambda s: False, nudge=nudge, warn=warn, now_dt=_NOW,
                                nudged_seen={}, warned_seen={})
    assert nudged == [] and res["skipped_dead"] == ["cc-oeh"]


# ---- recipient eligibility (the ONE shared definition) ----

def test_skips_ineligible_recipients_human_and_operator():
    nudged, warned, nudge, warn = _collector()
    res = wbs.idle_unread_sweep(rows=[_row(1, "musa", 1000), _row(2, "operator", 1000)],
                                resolve_session=lambda a: "sess", pane_busy=lambda s: False,
                                nudge=nudge, warn=warn, now_dt=_NOW, nudged_seen={}, warned_seen={})
    assert nudged == []


def test_hub_narrow_floor_does_not_apply_here_hub_never_nudged():
    # cc-orchestrator is eligible only on the CAI-451 P0/P1+rr floor for WAKING; this
    # detector has no priority signal at all (age-only), so treat the hub like any other
    # ineligible-by-default recipient (never nudge/wake the hub from here).
    nudged, warned, nudge, warn = _collector()
    res = wbs.idle_unread_sweep(rows=[_row(1, "cc-orchestrator", 1000)],
                                resolve_session=lambda a: "sess", pane_busy=lambda s: False,
                                nudge=nudge, warn=warn, now_dt=_NOW, nudged_seen={}, warned_seen={})
    assert nudged == []


def test_eligible_worker_and_console_are_nudged():
    nudged, warned, nudge, warn = _collector()
    wbs.idle_unread_sweep(rows=[_row(1, "cc-quality", 1000), _row(2, "orch-console", 1000),
                                _row(3, "cai", 1000)],
                          resolve_session=lambda a: "sess", pane_busy=lambda s: False,
                          nudge=nudge, warn=warn, now_dt=_NOW, nudged_seen={}, warned_seen={})
    assert {a for a, _ in nudged} == {"cc-quality", "orch-console", "cai"}


# ---- 30-min warn tier ----

def test_warns_fleet_health_once_past_30min_still_unread():
    nudged, warned, nudge, warn = _collector()
    res = wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 2000)], resolve_session=lambda a: "sess",  # ~33 min
                                pane_busy=lambda s: False, nudge=nudge, warn=warn, now_dt=_NOW,
                                nudged_seen={}, warned_seen={})
    assert nudged == [("cc-oeh", (1,))]
    assert warned == [("cc-oeh", (1,))]
    assert res["warned"] == ["cc-oeh"]


def test_does_not_warn_before_30min():
    nudged, warned, nudge, warn = _collector()
    wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 1000)], resolve_session=lambda a: "sess",  # ~16.7 min
                          pane_busy=lambda s: False, nudge=nudge, warn=warn, now_dt=_NOW,
                          nudged_seen={}, warned_seen={})
    assert warned == []


def test_warn_dedupes_across_sweeps():
    nudged, warned, nudge, warn = _collector()
    seen_w = {}
    kw = dict(rows=[_row(1, "cc-oeh", 2000)], resolve_session=lambda a: "sess",
              pane_busy=lambda s: False, nudge=nudge, warn=warn, now_dt=_NOW,
              nudged_seen={}, warned_seen=seen_w)
    wbs.idle_unread_sweep(**kw)
    wbs.idle_unread_sweep(**kw)
    assert len(warned) == 1


def test_warn_fires_again_when_a_new_id_crosses_30min():
    nudged, warned, nudge, warn = _collector()
    seen_w = {}
    kw1 = dict(resolve_session=lambda a: "sess", pane_busy=lambda s: False,
              nudge=nudge, warn=warn, now_dt=_NOW, nudged_seen={}, warned_seen=seen_w)
    wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 2000)], **kw1)
    wbs.idle_unread_sweep(rows=[_row(1, "cc-oeh", 2000), _row(2, "cc-oeh", 1900)], **kw1)
    assert len(warned) == 2 and warned[1] == ("cc-oeh", (1, 2))


# ---- delivery uses the normal doorbell, never raw send-keys ----

def test_default_nudge_posts_a_bus_row_and_calls_wake_agent_never_send_keys(monkeypatch):
    posted = []
    woke = []

    def fake_bus_insert(from_agent, to_agent, mtype, priority, subject, body, req=False):
        posted.append((from_agent, to_agent, mtype, priority, subject, body))
        return 999

    def fake_wake(agent, reason="", row_id=None):
        woke.append((agent, reason, row_id))
        return {"woke": True}

    monkeypatch.setattr(wbs, "_bus_insert", fake_bus_insert)
    wbs._default_nudge("cc-oeh", [46766], wake=fake_wake)
    assert len(posted) == 1
    frm, to, mtype, prio, subject, body = posted[0]
    assert frm == "cc-fleet-health" and to == "cc-oeh"
    assert "46766" in subject or "46766" in body
    assert woke == [("cc-oeh", "idle-unread-nudge", 999)]


def test_default_warn_posts_to_fleet_health(monkeypatch):
    posted = []

    def fake_bus_insert(from_agent, to_agent, mtype, priority, subject, body, req=False):
        posted.append((from_agent, to_agent, mtype, priority, subject, body))
        return 1000

    monkeypatch.setattr(wbs, "_bus_insert", fake_bus_insert)
    wbs._default_warn_fleet_health("cc-oeh", [46766])
    assert len(posted) == 1
    frm, to, mtype, prio, subject, body = posted[0]
    assert frm == "cc-fleet-health" and to == "cc-fleet-health"
    assert "cc-oeh" in subject and "46766" in body


def test_no_raw_send_keys_anywhere_in_the_module():
    import inspect
    src = inspect.getsource(wbs)
    assert "send-keys" not in src


# ---- wired into the daemon loop ("near the wake-backstop sweep") ----

def test_wired_into_the_daemon_loop():
    import inspect
    src = inspect.getsource(wbs.main)
    assert "idle_unread_sweep" in src
