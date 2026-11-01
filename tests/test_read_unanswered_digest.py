"""Acceptance tests for the read-but-unanswered digest (orch-console #88653/#88660).

The digest is REPORT-ONLY and must never claim more than it measured. The tests pin
the three claims that matter:

  1. A lane that is pane-DRAINED and owns such rows is reported as IDLE.
  2. A lane whose pane is BUSY is NOT reported as idle (it is counted as omitted).
  3. A lane we could not probe is NOT reported as idle either — 'could not measure'
     is its own state, never a negative finding (verify-not-assert).

Pure core, no DB, no tmux: collect() is exercised for the pane-state routing and
render() for the text, with read_unanswered/live_agents/lane_map stubbed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import read_unanswered_digest as d  # noqa: E402


def _row(i, age_h=10.0, pri="P1", frm="orch-console", subj="a question"):
    """One DB row in read_unanswered's column order:
    (id, from_agent, message_type, priority, subject, age_h)."""
    return (i, frm, "question", pri, subj, age_h)


def _entry_row(i, age_h=10.0, pri="P1", frm="orch-console", subj="a question"):
    """The dict form collect() puts in an entry's rows[] (for direct render tests)."""
    return {"id": i, "from": frm, "type": "question", "priority": pri,
            "subject": subj, "age_h": age_h}


def _patch(monkeypatch, agents, rows_by_agent, pane):
    monkeypatch.setattr(d, "live_agents", lambda cur, mh: agents)
    monkeypatch.setattr(d, "read_unanswered", lambda cur, a, h, p: rows_by_agent.get(a, []))
    monkeypatch.setattr(d, "lane_map", lambda cur: {"cc-a": "sess-a", "cc-b": "sess-b"})
    monkeypatch.setattr(d, "pane_state", lambda s: pane.get(s, "probe_error"))
    monkeypatch.setattr(d, "local_hostnames", lambda: {"Sheikhs-Mini"})


def test_drained_lane_is_idle(monkeypatch):
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 2)],
           {"cc-a-1": [_row(1)]},
           {"sess-a": "drained"})
    out = d.collect(cur=None)
    assert [e["agent"] for e in out["idle"]] == ["cc-a-1"]
    assert out["not_probeable"] == [] and out["busy"] == 0


def test_busy_lane_is_not_idle(monkeypatch):
    """A working lane owes the reply but is demonstrably not idle — count, don't list."""
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 1)],
           {"cc-a-1": [_row(1)]},
           {"sess-a": "busy"})
    out = d.collect(cur=None)
    assert out["idle"] == [] and out["not_probeable"] == [] and out["busy"] == 1


def test_unprobeable_lane_is_not_idle(monkeypatch):
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "gzbai", 1)],
           {"cc-a-1": [_row(1)]},
           {"sess-a": "drained"})  # would be drained, but it is on ANOTHER host
    out = d.collect(cur=None)
    assert out["idle"] == []
    assert [e["agent"] for e in out["not_probeable"]] == ["cc-a-1"]
    assert out["not_probeable"][0]["pane"] == "not_probeable"


def test_probe_error_is_not_idle(monkeypatch):
    _patch(monkeypatch,
           [("cc-a-1", "cc-a", "Sheikhs-Mini", 1)],
           {"cc-a-1": [_row(1)]},
           {"sess-a": "probe_error"})
    out = d.collect(cur=None)
    assert out["idle"] == []
    assert out["not_probeable"][0]["pane"] == "probe_error"


def test_lane_with_no_rows_is_absent(monkeypatch):
    _patch(monkeypatch, [("cc-a-1", "cc-a", "Sheikhs-Mini", 1)], {}, {})
    out = d.collect(cur=None)
    assert out["idle"] == [] and out["not_probeable"] == [] and out["busy"] == 0


def test_self_excluded_by_default_but_flag_restores(monkeypatch):
    _patch(monkeypatch,
           [(d.SELF_AGENT, "cc-fleet-health", "Sheikhs-Mini", 1)],
           {d.SELF_AGENT: [_row(9)]},
           {})
    assert d.collect(cur=None)["not_probeable"] == []
    assert [e["agent"] for e in d.collect(cur=None, include_self=True)["not_probeable"]] \
        == [d.SELF_AGENT]


def test_render_empty_says_empty_not_reassuring(monkeypatch):
    """An empty digest must say nothing was found — not manufacture a health claim."""
    text = d.render({"generated_at": "T", "thresholds": {"min_age_h": 6,
                                                         "priorities": ["P1"],
                                                         "max_heartbeat_min": 30},
                     "idle": [], "not_probeable": [], "busy": 0})
    assert "Nothing to report" in text


def test_render_bounds_rows_per_lane():
    entry = {"agent": "cc-a-1", "base": "cc-a", "host": "Sheikhs-Mini",
             "hb_age_min": 1, "pane": "drained", "session": "sess-a",
             "rows": [_entry_row(i, age_h=100 - i) for i in range(10)]}
    text = d.render({"generated_at": "T",
                     "thresholds": {"min_age_h": 6, "priorities": ["P1"],
                                    "max_heartbeat_min": 30},
                     "idle": [entry], "not_probeable": [], "busy": 0})
    assert "... +7 more row(s)" in text
    assert text.count("#1  ") <= 1
    assert "#8  " not in text          # the 9th row is summarised, not printed


def test_render_includes_stamp_gap_caveat():
    entry = {"agent": "cc-a-1", "base": "cc-a", "host": "Sheikhs-Mini",
             "hb_age_min": 1, "pane": "drained", "session": "sess-a",
             "rows": [_entry_row(1)]}
    text = d.render({"generated_at": "T",
                     "thresholds": {"min_age_h": 6, "priorities": ["P1"],
                                    "max_heartbeat_min": 30},
                     "idle": [entry], "not_probeable": [], "busy": 0})
    assert "stamping gap" in text
